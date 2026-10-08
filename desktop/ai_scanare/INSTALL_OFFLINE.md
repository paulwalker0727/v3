# 🔌 Activare mod offline (Tesseract OCR)

Modulul AI Scanare are un **fallback offline** care se activează automat când:
- Nu ai internet (Gemini nu răspunde)
- Cheia `EMERGENT_LLM_KEY` lipsește sau e invalidă
- Utilizatorul bifează „🔌 Doar offline" în dialogul de scanare

Precizia scade față de Gemini (60-80% vs 95-99%), dar funcționează 100% local.

## Instalare Tesseract OCR

### 🪟 Windows
1. Descarcă instalatorul de pe: https://github.com/UB-Mannheim/tesseract/wiki
2. La instalare bifează **Romanian** din listele de limbi (Language data → Additional)
3. Verifică calea (implicit): `C:\Program Files\Tesseract-OCR\tesseract.exe`
4. Adaugă folderul în `PATH` (Panou control → Sistem → Variabile de mediu)
5. Sau, dacă preferi, setează calea explicit în cod:
   ```python
   import pytesseract
   pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
   ```

### 🐧 Linux (Debian / Ubuntu)
```bash
sudo apt-get install tesseract-ocr tesseract-ocr-ron
```

### 🍎 macOS
```bash
brew install tesseract tesseract-lang
```

## Verificare

După instalare rulează în terminal:
```bash
tesseract --version
tesseract --list-langs   # ar trebui să vezi "ron" în listă
```

Sau în Python:
```python
from ai_scanare.offline_ocr import tesseract_available, has_romanian
print(tesseract_available())   # True
print(has_romanian())          # True
```

## Comportament automat
- Bifează „🔌 Doar offline" în dialogul de scanare → forțează Tesseract
- Lasă nebifat → încearcă Gemini întâi, apoi fallback automat la Tesseract dacă:
  - fără internet
  - cheia Gemini invalidă / expirată
  - quota Gemini depășită

Badge-ul din dialog îți spune ce engine a fost folosit (🤖 Gemini sau 🔌 Tesseract).
