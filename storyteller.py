import sys
import json
import re
from html import escape
import requests
import threading
import time
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QSpinBox, QLineEdit, QLabel, QComboBox,
    QSplitter, QMessageBox, QFileDialog, QCheckBox, QDoubleSpinBox, QTabWidget
)
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage

import markdown
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT


THINKING_TAG_NAME = r"[^>\s]*(?:think|reason|analysis|thought|scratch|reflection)[^>\s]*"
REASONING_EFFORT = "low"
SYSTEM_PROMPT = (
    "You are an expert story writer. Create vivid, imaginative scenes with emotional "
    "clarity, sensory detail, and purposeful character action. Use medium-length, "
    "varied sentences. Maintain narrative continuity, develop the requested premise, "
    "and avoid repetition."
)
PROMPT_LANGUAGES = {
    "english": "English",
    "german": "German",
    "french": "French",
}


class RequestSettings:
    """Thread-safe request settings shared by the UI and generation worker."""

    def __init__(self, thinking_enabled=False, temperature=0.7):
        self._lock = threading.Lock()
        self.thinking_enabled = thinking_enabled
        self.temperature = temperature

    def update(self, thinking_enabled, temperature):
        with self._lock:
            self.thinking_enabled = thinking_enabled
            self.temperature = temperature

    def snapshot(self):
        with self._lock:
            return self.thinking_enabled, self.temperature


def chat_completions_url(api_url):
    """Return the chat-completions endpoint for local and online APIs."""
    api_url = api_url.rstrip("/")
    if api_url.endswith("v1"):
        return f"{api_url}/chat/completions"
    return api_url


def extract_final_content(message):
    """Return user-facing content while discarding model reasoning fields and tags."""
    if not isinstance(message, dict):
        return ""

    content = message.get("content") or message.get("final") or message.get("output_text") or ""
    if isinstance(content, list):
        content = "".join(
            "" if isinstance(part, dict) and re.search(
                THINKING_TAG_NAME, part.get("type", ""), re.IGNORECASE
            ) else part.get("text", part.get("content", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    if not isinstance(content, str):
        content = str(content)

    tag_pattern = rf"<(?P<tag>{THINKING_TAG_NAME})(?:\s[^>]*)?>.*?</(?P=tag)\s*>"
    bracket_pattern = rf"\[(?P<tag>{THINKING_TAG_NAME})\].*?\[/(?P=tag)\]"
    content = re.sub(tag_pattern, "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(bracket_pattern, "", content, flags=re.IGNORECASE | re.DOTALL)
    return content.strip()


class AIWorker(QThread):
    """Thread to handle AI requests without blocking the UI."""
    progress_signal = Signal(str, str, str)  # chapter, section, content
    outline_signal = Signal(str, str)  # parsed outline JSON, original response text
    finished_signal = Signal(bool, str)  # success, message
    error_signal = Signal(str)
    outline_warning_signal = Signal(str)

    def __init__(self, base_url, model_name, prompt, num_chapters, num_sections, language,
                 api_key=None, thinking_enabled=False, temperature=0.7, request_settings=None):
        super().__init__()
        self.base_url = base_url
        self.model_name = model_name
        self.prompt = prompt
        self.num_chapters = num_chapters
        self.num_sections = num_sections
        self.language = language
        self.api_key = api_key
        self.request_settings = request_settings or RequestSettings(
            thinking_enabled, temperature
        )
        self.is_cancelled = False
        self.outline = []
        self.story = {}  # key: "chapter_section", value: content
        self.outline_parse_error = ""
        # Full conversation history — each entry is a dict with role/content
        self.history = []

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        try:
            # Step 1: Generate outline
            outline_prompt = self.build_outline_prompt()

            # Send outline request with no prior history (it's the first call)
            messages = [{"role": "user", "content": outline_prompt}]
            response = self.send_ai_request(messages)
            if self.is_cancelled:
                return

            # Append to history
            self.history.append({"role": "user", "content": outline_prompt})
            self.history.append({"role": "assistant", "content": response})

            # Parse outline
            self.outline = self.parse_outline(response)
            if not self.outline:
                self.outline_warning_signal.emit(
                    f"Failed to parse outline: {self.outline_parse_error}. "
                    "Using fallback structure."
                )
                self.outline = []
                for ch in range(1, self.num_chapters + 1):
                    sections = []
                    for sec in range(1, self.num_sections + 1):
                        sections.append({"title": f"Kapitel {ch} - Sektion {sec}", "desc": "Content"})
                    self.outline.append({"chapter": ch, "sections": sections})
            elif self.outline_parse_error:
                self.outline_warning_signal.emit(self.outline_parse_error)

            self.outline_signal.emit(json.dumps(self.outline, indent=2), response)

            # Step 2: Generate story chapter by chapter, section by section
            for ch_idx, chapter in enumerate(self.outline):
                if self.is_cancelled:
                    break
                chapter_num = chapter.get("chapter", ch_idx + 1)
                sections = chapter.get("sections", [])

                for sec_idx, section in enumerate(sections):
                    if self.is_cancelled:
                        break
                    section_num = sec_idx + 1
                    section_title = section.get("title", f"Sektion {section_num}")
                    section_desc = section.get("desc", "")

                    # Build prompt for this section
                    prompt = self.build_section_prompt(
                        chapter_num, section_num, section_title, section_desc
                    )

                    # Full history as messages: all prior user+assistant turns,
                    # then the current user prompt
                    messages = list(self.history) + [{"role": "user", "content": prompt}]

                    content = self.send_ai_request(messages)
                    if self.is_cancelled:
                        break

                    # Append current exchange to history
                    self.history.append({"role": "user", "content": prompt})
                    self.history.append({"role": "assistant", "content": content})

                    key = f"{chapter_num}_{section_num}"
                    self.story[key] = content

                    self.progress_signal.emit(str(chapter_num), str(section_num), content)

            if not self.is_cancelled:
                self.finished_signal.emit(True, "Story generation complete.")
            else:
                self.finished_signal.emit(True, "Story generation cancelled.")

        except Exception as e:
            self.error_signal.emit(str(e))

    def send_ai_request(self, messages):
        """Send request to an OpenAI-compatible AI API with full conversation history."""
        url = chat_completions_url(self.base_url)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        thinking_enabled, temperature = self.request_settings.snapshot()
        payload = {
            "model": self.model_name,
            "system": SYSTEM_PROMPT,
            "messages": messages,
            "temperature": temperature,
        }
        if thinking_enabled:
            payload["reasoning_effort"] = REASONING_EFFORT
        else:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            response.raise_for_status()
            data = response.json()
            message = data.get("choices", [{}])[0].get("message", {})
            return extract_final_content(message)
        except Exception as e:
            return f"Error: {str(e)}"

    def build_outline_prompt(self):
        prompt_language = PROMPT_LANGUAGES.get(self.language.lower())
        if prompt_language == "German":
            return (
                f"Erstelle eine Outline fuer eine Geschichte mit {self.num_chapters} Kapiteln "
                f"und jeweils {self.num_sections} Sektionen pro Kapitel. Thema: {self.prompt}. "
                "Gib die Outline als JSON-Liste zurueck, wobei jedes Element ein Kapitel ist "
                "und jedes Kapitel eine Liste von Sektionen enthaelt. Jede Sektion hat einen "
                "Titel und eine kurze Beschreibung. Schreibe die Geschichte auf Deutsch."
            )
        if prompt_language == "French":
            return (
                f"Cree un plan pour une histoire de {self.num_chapters} chapitres avec "
                f"{self.num_sections} sections par chapitre. Sujet : {self.prompt}. "
                "Retourne le plan sous forme de liste JSON, ou chaque element est un chapitre "
                "contenant une liste de sections. Chaque section doit avoir un titre et une "
                "courte description. Ecris l'histoire en francais."
            )
        return (
            f"Create an outline for a story with {self.num_chapters} chapters and "
            f"{self.num_sections} sections per chapter. Theme: {self.prompt}. Return the "
            "outline as a JSON list where each item is a chapter containing a list of sections. "
            "Each section needs a title and a short description. "
            f"Write the story in {self.language}."
        )

    def build_section_prompt(self, chapter_num, section_num, section_title, section_desc):
        prompt_language = PROMPT_LANGUAGES.get(self.language.lower())
        if prompt_language == "German":
            return (
                f"Schreibe Kapitel {chapter_num}, Sektion {section_num}: {section_title}. "
                f"{section_desc}. Schreibe ohne Wiederholungen. Gib nur den Text der Sektion "
                "zurueck. Schreibe auf Deutsch."
            )
        if prompt_language == "French":
            return (
                f"Ecris le chapitre {chapter_num}, section {section_num} : {section_title}. "
                f"{section_desc}. Evite les repetitions. Retourne uniquement le texte de la "
                "section. Ecris en francais."
            )
        return (
            f"Write chapter {chapter_num}, section {section_num}: {section_title}. "
            f"{section_desc}. Avoid repetition. Return only the section text. "
            f"Write in {self.language}."
        )

    def parse_outline(self, response):
        """Extract an outline, reporting whether a positional recovery was required."""
        self.outline_parse_error = ""
        if not isinstance(response, str):
            self.outline_parse_error = "The model response is not text."
            return None

        cleaned_response = re.sub(
            r"```(?:json)?\s*|```", "", response, flags=re.IGNORECASE
        ).strip()
        decoder = json.JSONDecoder()
        parse_errors = []

        for match in re.finditer(r"\[", cleaned_response):
            try:
                data, _ = decoder.raw_decode(cleaned_response[match.start():])
            except json.JSONDecodeError as error:
                parse_errors.append(
                    f"invalid JSON at character {match.start() + error.pos}: {error.msg}"
                )
                continue
            if isinstance(data, list):
                normalized_outline = self.normalize_outline(data)
                if normalized_outline:
                    return normalized_outline
                parse_errors.append("the JSON array does not contain recognizable chapters")

        recovered_outline = self.parse_outline_by_position(cleaned_response)
        if recovered_outline:
            reason = parse_errors[0] if parse_errors else "no JSON array was found"
            self.outline_parse_error = (
                f"Outline JSON could not be parsed strictly ({reason}). "
                "Recovered titles and descriptions by their positions in section objects."
            )
            return recovered_outline

        description_outline = self.parse_outline_by_description_tags(cleaned_response)
        if description_outline:
            reason = parse_errors[0] if parse_errors else "no JSON array was found"
            self.outline_parse_error = (
                f"Outline JSON could not be parsed strictly ({reason}). "
                "Recovered chapters and sections from description tags."
            )
            return description_outline

        self.outline_parse_error = (
            parse_errors[0] if parse_errors else "No JSON array or recoverable section objects were found"
        )
        return None

    def parse_outline_by_position(self, response):
        """Recover title/description pairs from malformed JSON section objects."""
        sections = []
        for object_text in re.findall(r"\{[^{}]*\}", response, flags=re.DOTALL):
            values = re.findall(r':\s*("(?:\\.|[^"\\])*")', object_text)
            if not values:
                continue
            decoded_values = []
            for value in values:
                try:
                    decoded_values.append(json.loads(value))
                except json.JSONDecodeError:
                    continue
            if decoded_values:
                sections.append({
                    "title": str(decoded_values[0]),
                    "desc": str(decoded_values[1]) if len(decoded_values) > 1 else "",
                })

        expected_sections = self.num_chapters * self.num_sections
        if len(sections) < expected_sections:
            return None

        recovered_outline = []
        for chapter_number in range(1, self.num_chapters + 1):
            start = (chapter_number - 1) * self.num_sections
            recovered_outline.append({
                "chapter": chapter_number,
                "sections": sections[start:start + self.num_sections],
            })
        return recovered_outline

    def parse_outline_by_description_tags(self, response):
        """Recover an outline from XML-like description tags in a model response."""
        descriptions = [
            re.sub(r"\s+", " ", description).strip()
            for description in re.findall(
                r"<description(?:\s[^>]*)?>(.*?)</description\s*>",
                response,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if description.strip()
        ]
        if not descriptions:
            return None

        recovered_outline = []
        for start in range(0, len(descriptions), self.num_sections):
            chapter_number = len(recovered_outline) + 1
            sections = []
            for section_number, description in enumerate(
                descriptions[start:start + self.num_sections], start=1
            ):
                sections.append({
                    "title": f"Section {section_number}",
                    "desc": description,
                })
            recovered_outline.append({"chapter": chapter_number, "sections": sections})
        return recovered_outline

    def normalize_outline(self, outline):
        """Convert localized outline keys to the schema used by story generation."""
        chapter_keys = ("chapter", "chapter_title", "kapitel", "Kapitel", "chapitre", "capitulo", "capitulo")
        section_keys = ("sections", "sektionen", "Sektionen", "sections", "secciones")
        title_keys = ("title", "section_title", "titel", "Titel", "titre", "titulo")
        description_keys = ("desc", "description", "beschreibung", "Beschreibung", "descripcion")

        normalized_outline = []
        for chapter_index, chapter in enumerate(outline, start=1):
            if not isinstance(chapter, dict):
                continue

            chapter_value = next(
                (chapter[key] for key in chapter_keys if key in chapter),
                next((value for value in chapter.values() if not isinstance(value, (dict, list))), chapter_index)
            )
            if isinstance(chapter_value, int):
                chapter_number = chapter_value
            else:
                chapter_number_match = re.search(r"\d+", str(chapter_value))
                chapter_number = (
                    int(chapter_number_match.group())
                    if chapter_number_match else chapter_index
                )
            sections = next(
                (chapter[key] for key in section_keys if key in chapter),
                next((value for value in chapter.values() if isinstance(value, list)), [])
            )
            if not isinstance(sections, list):
                continue

            normalized_sections = []
            for section_index, section in enumerate(sections, start=1):
                if not isinstance(section, dict):
                    continue
                positional_values = [
                    value for value in section.values() if not isinstance(value, (dict, list))
                ]
                title = next(
                    (section[key] for key in title_keys if key in section),
                    positional_values[0] if positional_values else f"Section {section_index}"
                )
                description = next(
                    (section[key] for key in description_keys if key in section),
                    positional_values[1] if len(positional_values) > 1 else ""
                )
                normalized_sections.append({"title": title, "desc": description})

            normalized_outline.append(
                {"chapter": chapter_number, "sections": normalized_sections}
            )

        return normalized_outline

    def save_story(self):
        """Return the full story as a string."""
        full_story = []
        for ch_idx, chapter in enumerate(self.outline):
            chapter_num = chapter.get("chapter", ch_idx + 1)
            full_story.append(f"\n# Kapitel {chapter_num}\n")
            for sec_idx, section in enumerate(chapter.get("sections", [])):
                section_num = sec_idx + 1
                key = f"{chapter_num}_{section_num}"
                content = self.story.get(key, "")
                title = section.get("title", f"Sektion {section_num}")
                full_story.append(f"\n## {title}\n\n{content}\n")
        return "".join(full_story)


class StoryGenerator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Story Generator")
        self.setGeometry(100, 100, 1200, 800)
        self.base_url = "http://127.0.0.1:8080/v1"
        self.model_name = "local"
        self.api_key = None
        self.worker = None
        self.outline = []
        self.outline_response = ""
        self.story = {}  # key: "chapter_section", value: content
        self.generation_started_at = None
        self.story_language = "English"
        self.request_settings = RequestSettings()
        # Conversation history stored on the main window for regenerate use
        self.history = []

        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QHBoxLayout(central_widget)

        # Left panel - controls
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        left_layout.addWidget(QLabel("API URL:"))
        self.api_url_input = QLineEdit(self.base_url)
        left_layout.addWidget(self.api_url_input)

        left_layout.addWidget(QLabel("Model Name:"))
        self.model_name_input = QLineEdit(self.model_name)
        left_layout.addWidget(self.model_name_input)

        left_layout.addWidget(QLabel("API Key (optional):"))
        self.api_key_input = QLineEdit()
        left_layout.addWidget(self.api_key_input)

        thinking_temperature_layout = QHBoxLayout()
        self.thinking_checkbox = QCheckBox("Enable thinking")
        thinking_temperature_layout.addWidget(self.thinking_checkbox)
        thinking_temperature_layout.addWidget(QLabel("Temperature:"))
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setDecimals(1)
        self.temperature_spin.setValue(0.7)
        thinking_temperature_layout.addWidget(self.temperature_spin)
        left_layout.addLayout(thinking_temperature_layout)
        self.thinking_checkbox.stateChanged.connect(self.update_request_settings)
        self.temperature_spin.valueChanged.connect(self.update_request_settings)

        left_layout.addWidget(QLabel("Story Idea:"))
        self.prompt_input = QTextEdit()
        self.prompt_input.setMaximumHeight(100)
        left_layout.addWidget(self.prompt_input)

        left_layout.addWidget(QLabel("Story Language:"))
        self.language_combo = QComboBox()
        self.language_combo.setEditable(True)
        self.language_combo.addItems(["English", "German", "French"])
        left_layout.addWidget(self.language_combo)

        left_layout.addWidget(QLabel("Number of Chapters:"))
        self.chapters_spin = QSpinBox()
        self.chapters_spin.setMinimum(1)
        self.chapters_spin.setMaximum(50)
        self.chapters_spin.setValue(10)
        left_layout.addWidget(self.chapters_spin)

        left_layout.addWidget(QLabel("Sections per Chapter:"))
        self.sections_spin = QSpinBox()
        self.sections_spin.setMinimum(1)
        self.sections_spin.setMaximum(20)
        self.sections_spin.setValue(4)
        left_layout.addWidget(self.sections_spin)

        self.generate_btn = QPushButton("Generate Story")
        self.generate_btn.clicked.connect(self.generate_story)
        left_layout.addWidget(self.generate_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_generation)
        self.cancel_btn.setEnabled(False)
        left_layout.addWidget(self.cancel_btn)

        left_layout.addWidget(QLabel("Regenerate Section:"))
        self.regenerate_chapter_combo = QComboBox()
        self.regenerate_section_combo = QComboBox()
        left_layout.addWidget(self.regenerate_chapter_combo)
        left_layout.addWidget(self.regenerate_section_combo)
        self.regenerate_btn = QPushButton("Regenerate Selected Section")
        self.regenerate_btn.clicked.connect(self.regenerate_section)
        self.regenerate_btn.setEnabled(False)
        left_layout.addWidget(self.regenerate_btn)

        self.save_pdf_btn = QPushButton("Save as PDF")
        self.save_pdf_btn.clicked.connect(self.save_as_pdf)
        self.save_pdf_btn.setEnabled(False)
        left_layout.addWidget(self.save_pdf_btn)

        self.save_markdown_btn = QPushButton("Save as Markdown")
        self.save_markdown_btn.clicked.connect(self.save_as_markdown)
        self.save_markdown_btn.setEnabled(False)
        left_layout.addWidget(self.save_markdown_btn)

        self.save_outline_btn = QPushButton("Save Outline")
        self.save_outline_btn.clicked.connect(self.save_outline)
        self.save_outline_btn.setEnabled(False)
        left_layout.addWidget(self.save_outline_btn)

        self.chapter_status_label = QLabel("Current chapter: -")
        self.section_status_label = QLabel("Current section: -")
        self.characters_status_label = QLabel("Generated characters: 0")
        self.speed_status_label = QLabel("LLM speed: -")
        left_layout.addWidget(self.chapter_status_label)
        left_layout.addWidget(self.section_status_label)
        left_layout.addWidget(self.characters_status_label)
        left_layout.addWidget(self.speed_status_label)

        left_layout.addStretch()

        # Right panel - story and outline previews
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        self.preview_tabs = QTabWidget()
        self.story_view = QWebEngineView()
        self.outline_view = QWebEngineView()
        self.preview_tabs.addTab(self.story_view, "Story")
        self.preview_tabs.addTab(self.outline_view, "Outline")
        right_layout.addWidget(self.preview_tabs)

        layout.addWidget(left_widget, 1)
        layout.addWidget(right_widget, 3)

        self.update_combo_boxes()

    def update_combo_boxes(self):
        self.regenerate_chapter_combo.clear()
        self.regenerate_section_combo.clear()
        for ch in range(1, self.chapters_spin.value() + 1):
            self.regenerate_chapter_combo.addItem(f"Chapter {ch}")
        for sec in range(1, self.sections_spin.value() + 1):
            self.regenerate_section_combo.addItem(f"Section {sec}")

    def update_request_settings(self, *_):
        self.request_settings.update(
            self.thinking_checkbox.isChecked(), self.temperature_spin.value()
        )

    def generate_story(self):
        self.base_url = self.api_url_input.text().strip()
        self.model_name = self.model_name_input.text().strip()
        self.api_key = self.api_key_input.text().strip()
        thinking_enabled = self.thinking_checkbox.isChecked()
        temperature = self.temperature_spin.value()
        self.request_settings.update(thinking_enabled, temperature)
        prompt = self.prompt_input.toPlainText().strip()
        num_chapters = self.chapters_spin.value()
        num_sections = self.sections_spin.value()
        self.story_language = self.language_combo.currentText().strip() or "English"

        if not prompt:
            QMessageBox.warning(self, "Warning", "Please enter a story idea.")
            return
        if not self.base_url:
            QMessageBox.warning(self, "Warning", "Please enter an API URL.")
            return
        if not self.model_name:
            QMessageBox.warning(self, "Warning", "Please enter a model name.")
            return

        # Clear previous story and history
        self.story = {}
        self.outline = []
        self.outline_response = ""
        self.history = []
        self.generation_started_at = time.monotonic()
        self.story_view.setHtml("")
        self.outline_view.setHtml("")
        self.update_combo_boxes()
        self.chapter_status_label.setText("Current chapter: -")
        self.section_status_label.setText("Current section: -")
        self.characters_status_label.setText("Generated characters: 0")
        self.speed_status_label.setText("LLM speed: -")

        # Start worker
        self.worker = AIWorker(
            self.base_url, self.model_name, prompt, num_chapters, num_sections,
            self.story_language, self.api_key, thinking_enabled, temperature,
            self.request_settings
        )
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.outline_signal.connect(self.on_outline)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.error_signal.connect(self.on_error)
        self.worker.outline_warning_signal.connect(self.on_outline_warning)
        self.worker.start()

        self.generate_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.save_pdf_btn.setEnabled(False)
        self.save_markdown_btn.setEnabled(False)
        self.save_outline_btn.setEnabled(False)
        self.regenerate_btn.setEnabled(False)

    def cancel_generation(self):
        if self.worker:
            self.worker.cancel()

    def on_progress(self, chapter, section, content):
        key = f"{chapter}_{section}"
        self.story[key] = content
        self.chapter_status_label.setText(f"Current chapter: {chapter}")
        self.section_status_label.setText(f"Current section: {section}")

        character_count = sum(len(section_content) for section_content in self.story.values())
        self.characters_status_label.setText(f"Generated characters: {character_count:,}")

        elapsed_seconds = time.monotonic() - self.generation_started_at
        if elapsed_seconds > 0:
            characters_per_second = character_count / elapsed_seconds
            self.speed_status_label.setText(f"LLM speed: {characters_per_second:.1f} chars/s")

        # Sync history from worker so regenerate can use it
        if self.worker:
            self.history = list(self.worker.history)
        self.update_markdown_viewer()

    def on_outline(self, outline_json, outline_response):
        self.outline_response = outline_response
        self.update_outline_viewer()
        self.save_outline_btn.setEnabled(True)
        try:
            self.outline = json.loads(outline_json)
        except:
            pass

    def on_finished(self, success, message):
        # Ensure history is fully synced
        if self.worker:
            self.history = list(self.worker.history)
        self.generate_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.save_pdf_btn.setEnabled(True)
        self.save_markdown_btn.setEnabled(True)
        self.regenerate_btn.setEnabled(True)
        QMessageBox.information(self, "Done", message)

    def on_error(self, error):
        self.generate_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        QMessageBox.warning(self, "Error", error)

    def on_outline_warning(self, warning):
        QMessageBox.warning(self, "Outline Parsing Warning", warning)

    def update_markdown_viewer(self):
        if not self.outline:
            html = "<p>No story generated yet.</p>"
        else:
            md_content = []
            for ch_idx, chapter in enumerate(self.outline):
                chapter_num = chapter.get("chapter", ch_idx + 1)
                md_content.append(f"\n# Kapitel {chapter_num}\n")
                for sec_idx, section in enumerate(chapter.get("sections", [])):
                    section_num = sec_idx + 1
                    key = f"{chapter_num}_{section_num}"
                    content = self.story.get(key, "")
                    title = section.get("title", f"Sektion {section_num}")
                    md_content.append(f"\n## {title}\n\n{content}\n")

            md_text = "".join(md_content)
            html = markdown.markdown(md_text)
            css = """
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; }
                h1 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
                h2 { color: #3498db; margin-top: 20px; }
                p { line-height: 1.6; }
            </style>
            """
            self.set_html_preserving_scroll(self.story_view, f"{css}<html><body>{html}</body></html>")

    def update_outline_viewer(self):
        css = """
        <style>
            body { font-family: monospace; margin: 20px; white-space: pre-wrap; }
        </style>
        """
        html = f"{css}<html><body>{escape(self.outline_response)}</body></html>"
        self.set_html_preserving_scroll(self.outline_view, html)

    def set_html_preserving_scroll(self, view, html):
        def update_view(scroll_position):
            def restore_scroll(_success):
                view.loadFinished.disconnect(restore_scroll)
                view.page().runJavaScript(f"window.scrollTo(0, {scroll_position or 0});")

            view.loadFinished.connect(restore_scroll)
            view.setHtml(html)

        view.page().runJavaScript("window.scrollY", update_view)

    def save_outline(self):
        if not self.outline_response:
            QMessageBox.warning(self, "Warning", "No outline generated.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Outline", "", "Text Files (*.txt);;All Files (*)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as outline_file:
                outline_file.write(self.outline_response)
            QMessageBox.information(self, "Done", f"Outline saved to {file_path}")
        except OSError as error:
            QMessageBox.warning(self, "Error", str(error))

    def regenerate_section(self):
        chapter_idx = self.regenerate_chapter_combo.currentIndex()
        section_idx = self.regenerate_section_combo.currentIndex()
        chapter_num = chapter_idx + 1
        section_num = section_idx + 1

        if not self.outline:
            QMessageBox.warning(self, "Warning", "No story generated. Generate a story first.")
            return

        chapter = self.outline[chapter_idx]
        sections = chapter.get("sections", [])
        if section_idx >= len(sections):
            QMessageBox.warning(self, "Warning", "Section not found.")
            return

        section = sections[section_idx]
        section_title = section.get("title", f"Sektion {section_num}")
        section_desc = section.get("desc", "")

        # Build the prompt for regeneration
        prompt = self.build_section_prompt(
            chapter_num, section_num, section_title, section_desc
        )

        # Use full conversation history as context
        messages = list(self.history) + [{"role": "user", "content": prompt}]

        url = chat_completions_url(self.base_url)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        thinking_enabled, temperature = self.request_settings.snapshot()
        payload = {
            "model": self.model_name,
            "system": SYSTEM_PROMPT,
            "messages": messages,
            "temperature": temperature,
        }
        if thinking_enabled:
            payload["reasoning_effort"] = REASONING_EFFORT
        else:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            response.raise_for_status()
            data = response.json()
            message = data.get("choices", [{}])[0].get("message", {})
            content = extract_final_content(message)

            key = f"{chapter_num}_{section_num}"
            self.story[key] = content

            # Append this exchange to history
            self.history.append({"role": "user", "content": prompt})
            self.history.append({"role": "assistant", "content": content})

            self.update_markdown_viewer()
            QMessageBox.information(self, "Done",
                f"Section regenerated: Kapitel {chapter_num}, Sektion {section_num}")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def save_as_pdf(self):
        if not self.outline:
            QMessageBox.warning(self, "Warning", "No story generated.")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "Save PDF", "", "PDF Files (*.pdf)")
        if not file_path:
            return

        try:
            doc = SimpleDocTemplate(file_path, pagesize=A4)
            styles = getSampleStyleSheet()
            
            # Check if 'Normal' style already exists before adding
            existing_names = [style.name for style in styles.byName.values()] if hasattr(styles, 'byName') else [style.name for style in styles]
            
            if 'Normal' not in existing_names:
                styles.add(ParagraphStyle(name='Normal', fontName='Helvetica', fontSize=12,
                                         leading=14, alignment=TA_JUSTIFY))
            
            if 'H1' not in existing_names:
                styles.add(ParagraphStyle(name='H1', fontName='Helvetica-Bold', fontSize=16,
                                         leading=20, alignment=TA_CENTER, spaceBefore=20, spaceAfter=10))
            
            if 'H2' not in existing_names:
                styles.add(ParagraphStyle(name='H2', fontName='Helvetica-Bold', fontSize=14,
                                         leading=18, alignment=TA_LEFT, spaceBefore=15, spaceAfter=8))

            story_elements = []
            for ch_idx, chapter in enumerate(self.outline):
                chapter_num = chapter.get("chapter", ch_idx + 1)
                story_elements.append(Paragraph(f"Kapitel {chapter_num}", styles['H1']))
                story_elements.append(Spacer(1, 12))

                for sec_idx, section in enumerate(chapter.get("sections", [])):
                    section_num = sec_idx + 1
                    key = f"{chapter_num}_{section_num}"
                    content = self.story.get(key, "")
                    title = section.get("title", f"Sektion {section_num}")
                    story_elements.append(Paragraph(title, styles['H2']))
                    story_elements.append(Spacer(1, 6))
                    
                    # Split content by newlines to preserve them in the PDF
                    lines = content.split('\n')
                    for line in lines:
                        if line.strip():
                            story_elements.append(Paragraph(line, styles['Normal']))
                            story_elements.append(Spacer(1, 4))
                        else:
                            story_elements.append(Spacer(1, 6))
                    
                    story_elements.append(Spacer(1, 12))

                story_elements.append(PageBreak())

            doc.build(story_elements)
            QMessageBox.information(self, "Done", f"PDF saved to {file_path}")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def build_section_prompt(self, chapter_num, section_num, section_title, section_desc):
        prompt_language = PROMPT_LANGUAGES.get(self.story_language.lower())
        if prompt_language == "German":
            return (
                f"Schreibe Kapitel {chapter_num}, Sektion {section_num}: {section_title}. "
                f"{section_desc}. Schreibe ohne Wiederholungen. Gib nur den Text der Sektion "
                "zurueck. Schreibe auf Deutsch."
            )
        if prompt_language == "French":
            return (
                f"Ecris le chapitre {chapter_num}, section {section_num} : {section_title}. "
                f"{section_desc}. Evite les repetitions. Retourne uniquement le texte de la "
                "section. Ecris en francais."
            )
        return (
            f"Write chapter {chapter_num}, section {section_num}: {section_title}. "
            f"{section_desc}. Avoid repetition. Return only the section text. "
            f"Write in {self.story_language}."
        )

    def save_as_markdown(self):
        if not self.outline:
            QMessageBox.warning(self, "Warning", "No story generated.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Markdown", "", "Markdown Files (*.md);;All Files (*)"
        )
        if not file_path:
            return

        markdown_content = []
        for ch_idx, chapter in enumerate(self.outline):
            chapter_num = chapter.get("chapter", ch_idx + 1)
            markdown_content.append(f"# Kapitel {chapter_num}\n")

            for sec_idx, section in enumerate(chapter.get("sections", [])):
                section_num = sec_idx + 1
                key = f"{chapter_num}_{section_num}"
                title = section.get("title", f"Sektion {section_num}")
                content = self.story.get(key, "")
                markdown_content.append(f"\n## {title}\n\n{content}\n")

        try:
            with open(file_path, "w", encoding="utf-8") as markdown_file:
                markdown_file.write("".join(markdown_content))
            QMessageBox.information(self, "Done", f"Markdown saved to {file_path}")
        except OSError as error:
            QMessageBox.warning(self, "Error", str(error))


def main():
    app = QApplication(sys.argv)
    window = StoryGenerator()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()