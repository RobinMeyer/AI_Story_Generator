import sys
import json
import requests
import threading
import time
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QSpinBox, QLineEdit, QLabel, QComboBox,
    QSplitter, QMessageBox, QFileDialog, QCheckBox
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


class AIWorker(QThread):
    """Thread to handle AI requests without blocking the UI."""
    progress_signal = Signal(str, str, str)  # chapter, section, content
    outline_signal = Signal(str)  # outline text
    finished_signal = Signal(bool, str)  # success, message
    error_signal = Signal(str)

    def __init__(self, base_url, prompt, num_chapters, num_sections, api_key=None):
        super().__init__()
        self.base_url = base_url
        self.prompt = prompt
        self.num_chapters = num_chapters
        self.num_sections = num_sections
        self.api_key = api_key
        self.is_cancelled = False
        self.outline = []
        self.story = {}  # key: "chapter_section", value: content
        # Full conversation history — each entry is a dict with role/content
        self.history = []

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        try:
            # Step 1: Generate outline
            outline_prompt = (
                f"Erstelle eine Outline für eine Geschichte mit {self.num_chapters} Kapiteln "
                f"und jeweils {self.num_sections} Sektionen pro Kapitel. "
                f"Thema: {self.prompt}. Gib die Outline als JSON-Liste zurück, "
                f"wobei jedes Element ein Kapitel ist und jedes Kapitel eine Liste von "
                f"Sektionen enthält. Jede Sektion hat einen Titel und eine kurze Beschreibung."
            )

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
                self.error_signal.emit("Failed to parse outline. Using fallback structure.")
                self.outline = []
                for ch in range(1, self.num_chapters + 1):
                    sections = []
                    for sec in range(1, self.num_sections + 1):
                        sections.append({"title": f"Kapitel {ch} - Sektion {sec}", "desc": "Content"})
                    self.outline.append({"chapter": ch, "sections": sections})

            self.outline_signal.emit(json.dumps(self.outline, indent=2))

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
                    prompt = (
                        f"Schreibe Kapitel {chapter_num}, Sektion {section_num}: "
                        f"{section_title}. {section_desc}. "
                        f"Schreibe ohne Wiederholungen. Gib nur den Text der Sektion zurück."
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
        """Send request to local AI API with full conversation history."""
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": "qwen-3.8-27b",
            "messages": messages,
            "temperature": 0.7,
            "reasoning_effort": "none"
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            response.raise_for_status()
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content.strip()
        except Exception as e:
            return f"Error: {str(e)}"

    def parse_outline(self, response):
        """Try to parse outline from AI response."""
        try:
            import re
            # Try to find JSON array
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                data = json.loads(json_str)
                return data
            return None
        except:
            return None

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
        self.api_key = None
        self.worker = None
        self.outline = []
        self.story = {}  # key: "chapter_section", value: content
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

        left_layout.addWidget(QLabel("API Key (optional):"))
        self.api_key_input = QLineEdit()
        left_layout.addWidget(self.api_key_input)

        left_layout.addWidget(QLabel("Story Idea:"))
        self.prompt_input = QTextEdit()
        self.prompt_input.setMaximumHeight(100)
        left_layout.addWidget(self.prompt_input)

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

        left_layout.addStretch()

        # Right panel - markdown viewer
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        self.web_view = QWebEngineView()
        right_layout.addWidget(self.web_view)

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

    def generate_story(self):
        self.base_url = self.api_url_input.text().strip()
        self.api_key = self.api_key_input.text().strip()
        prompt = self.prompt_input.toPlainText().strip()
        num_chapters = self.chapters_spin.value()
        num_sections = self.sections_spin.value()

        if not prompt:
            QMessageBox.warning(self, "Warning", "Please enter a story idea.")
            return

        # Clear previous story and history
        self.story = {}
        self.outline = []
        self.history = []
        self.web_view.setHtml("")
        self.update_combo_boxes()

        # Start worker
        self.worker = AIWorker(self.base_url, prompt, num_chapters, num_sections, self.api_key)
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.outline_signal.connect(self.on_outline)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.error_signal.connect(self.on_error)
        self.worker.start()

        self.generate_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.save_pdf_btn.setEnabled(False)
        self.regenerate_btn.setEnabled(False)

    def cancel_generation(self):
        if self.worker:
            self.worker.cancel()

    def on_progress(self, chapter, section, content):
        key = f"{chapter}_{section}"
        self.story[key] = content
        # Sync history from worker so regenerate can use it
        if self.worker:
            self.history = list(self.worker.history)
        self.update_markdown_viewer()

    def on_outline(self, outline_json):
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
        self.regenerate_btn.setEnabled(True)
        QMessageBox.information(self, "Done", message)

    def on_error(self, error):
        self.generate_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        QMessageBox.warning(self, "Error", error)

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
            self.web_view.setHtml(f"{css}<html><body>{html}</body></html>")

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
        prompt = (
            f"Schreibe Kapitel {chapter_num}, Sektion {section_num}: "
            f"{section_title}. {section_desc}. "
            f"Schreibe ohne Wiederholungen. Gib nur den Text der Sektion zurück."
        )

        # Use full conversation history as context
        messages = list(self.history) + [{"role": "user", "content": prompt}]

        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": "qwen-3.8-27b",
            "messages": messages,
            "temperature": 0.7,
            "reasoning_effort": "none"
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            response.raise_for_status()
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

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


def main():
    app = QApplication(sys.argv)
    window = StoryGenerator()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()