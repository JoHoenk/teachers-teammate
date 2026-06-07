# Teacher's Teammate

Teacher's Teammate is a batch OCR pipeline that extracts text from scanned documents (PDFs and images) and plain text files (TXT), then produces proofread DOCX reports. It supports the full digitization workflow: OCR, optional anonymization of personal data before any cloud processing, LLM-based text correction, and evaluation of correction quality.

The application ships as a PySide6 desktop GUI and a command-line tool.

---

## Installation

```bash
pip install teachers-teammate
```

Optional privacy/anonymization support (requires spaCy):

```bash
pip install "teachers-teammate[privacy]"
python -m spacy download en_core_web_sm
```

---

## Quick start

```bash
# Launch the desktop GUI
teachers-teammate-gui

# Command-line batch processing (add --docx to also write DOCX reports)
teachers-teammate -i pdfs/ -o output/ --docx
```

DOCX output is opt-in on the command line: without `--docx`, only the per-file cache state is written.

---

## Ollama setup (recommended OCR engine)

Teacher's Teammate uses [Ollama](https://ollama.com) for OCR by default. Install Ollama separately if it is not already running on your machine:

```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh

# macOS or Windows — use the installer from https://ollama.com/download
```

Pull an OCR model:

```bash
ollama pull deepseek-ocr:latest
```

Then launch the GUI, set your input and output folders, and click **Run**. Settings are saved automatically to `~/.cache/teachers_teammate/ocr.toml` and reloaded on the next launch.

---

## Credits

- [Ollama-OCR](https://github.com/imanoop7/Ollama-OCR) by imanoop7 — inspired this project
- [Ollama](https://ollama.com) — local LLM inference runtime
- [LangChain](https://github.com/langchain-ai/langchain) — LLM abstraction layer
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) — open-source OCR engine
