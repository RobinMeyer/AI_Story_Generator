# AI Story Generator

A Python desktop application for creating long-form stories with AI assistance. Generate an outline, chapters, or individual sections independently, then continue refining any part of the story with additional prompts.

## Features

- Generate story ideas, outlines, chapters, and sections
- Continue or rewrite a selected section without regenerating the whole story
- Build longer stories through multiple focused AI requests
- Desktop user interface built with PySide6
- Keep API credentials outside source code with a local `.env` file

## Requirements

- Python 3.10 or newer
- An OpenAI API key

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

4. Create a `.env` file in the project root:

   ```env
   OPENAI_API_KEY=your_api_key_here
   ```

## Usage

Start the application with its Python entry-point script. Once running, provide a story concept and generate an outline, chapter, or section. Use follow-up requests to expand, revise, or continue the specific portion of the story you want to work on.

> The exact startup command depends on the entry-point script included in the project.

## Development

Install dependencies in an isolated virtual environment before making changes. Do not commit `.env` files or API keys.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).
