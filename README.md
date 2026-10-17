# AI Story Generator

A Python desktop application for creating long-form stories with AI assistance. Generate an outline, chapters, or individual sections independently, then continue refining any part of the story with additional prompts.
<img width="1202" height="832" alt="grafik" src="https://github.com/user-attachments/assets/e186d7f8-fcf2-4228-bb4d-896a4d91c187" />


## Features

- Generate story ideas, outlines, chapters, and sections
- Continue or rewrite a selected section without regenerating the whole story
- Build longer stories through multiple focused AI requests
- Desktop user interface built with PySide6
- Keep API credentials outside source code with a local `.env` file

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

Start the application with python .\storyteller.py in the venv. Once running, provide a story concept and generate an outline, chapter, or section. Use follow-up requests to expand, revise, or continue the specific portion of the story you want to work on.

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
