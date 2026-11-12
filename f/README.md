# Excel-Server

    pip install -r requirements.txt
    uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log

UI: http://localhost:8000/  -> Formel kopieren -> in Excel einfuegen.

Neue Funktion: Datei in modules/ anlegen, von `Module` erben (siehe modules/mwst.py).
Server neu starten, fertig -- UI und Formeln entstehen automatisch.
