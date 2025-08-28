# Time Tracker (Streamlit + Postgres)

Produktionsreife Variante mit PostgreSQL (z. B. Supabase/Neon). Tabellen werden beim Start automatisch erzeugt.

## Lokal starten
1) Env-Variablen setzen:
```bash
export DATABASE_URL="postgresql://USER:PASSWORD@HOST:PORT/DBNAME"
export ALLOWED_USERS="Noah,Elena,Timon,Stefan,Gast"
```
2) Install & Run
```bash
pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

## Streamlit Community Cloud
- Repo auf GitHub pushen
- Auf https://share.streamlit.io/ deployen (App file: `streamlit_app.py`)
- In **Settings → Secrets** setzen:
```
DATABASE_URL = postgresql://USER:PASSWORD@HOST:PORT/DBNAME
ALLOWED_USERS = Noah,Elena,Timon,Stefan,Gast
```

Die App erstellt Tabellen automatisch, wenn sie noch nicht existieren.
