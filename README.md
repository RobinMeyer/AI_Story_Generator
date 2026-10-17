# AI Story Generator

A Python desktop application for creating long-form stories with AI assistance. Generate an outline, chapters, or individual sections independently, then continue refining any part of the story with additional prompts.
<img width="1202" height="832" alt="grafik" src="https://github.com/user-attachments/assets/e186d7f8-fcf2-4228-bb4d-896a4d91c187" />


## Features

- Generate structured story outlines, chapters, and sections from a story idea
- Continue or rewrite a selected section without regenerating the whole story
- Build longer stories through multiple focused AI requests
- Choose English, German, French, or enter any custom story language
- Review the generated story and the original outline response in separate tabs
- Save the complete story as PDF or Markdown and save the original outline response as text
- Monitor the current chapter, section, generated characters, and average generation speed
- Use a dedicated system prompt to guide the model's writing style
- Desktop user interface built with PySide6
- Optional API key support for compatible chat-completions APIs

## Requirements

- Python 3.10 or newer

## Installation

1. Clone the repository and enter the project directory.
2. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   ```

   **Windows (PowerShell):**

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

   **macOS/Linux:**

   ```bash
   source .venv/bin/activate
   ```

3. Install the dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Usage

Start the application with:

```powershell
python .\storyteller.py
```

Enter the API URL, optional API key, story idea, language, chapter count, and sections per chapter, then select **Generate Story**. The language menu includes English, German, and French, but you can type another language; the application then uses English instructions while asking the model to write in your chosen language.

The right pane has separate **Story** and **Outline** tabs. Each keeps its scroll position while new story sections are generated. The outline tab displays the original model response, which can also be saved with **Save Outline**. Use **Save as PDF** or **Save as Markdown** to export the generated story.

During generation, the status lines show the most recently completed chapter and section, total generated characters, and the average output speed in characters per second. You can cancel a run or regenerate a selected section after generation completes.

For the local LLM, I propose using https://github.com/Niko1221/Strata, because larger LLM's are better for stories. Cloud LLM was not successfully tested yet.

## Development

Install dependencies in an isolated virtual environment before making changes. Do not commit `.env` files or API keys.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

## Third-party licenses

This application uses PySide6 (Qt for Python). PySide6 and the Qt libraries are
provided under their respective LGPLv3/GPLv3 or commercial licensing terms.
They are not relicensed by this project. When distributing a bundled executable
or archive that includes PySide6/Qt libraries, include the applicable Qt,
PySide6, and third-party license notices with that distribution.
