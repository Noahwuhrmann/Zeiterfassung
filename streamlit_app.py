import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from sqlalchemy import (
    create_engine, Integer, String, Text, ForeignKey,
    select, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, relationship
from sqlalchemy.exc import SQLAlchemyError, IntegrityError

# ---------------- CONFIG ----------------
TZ = ZoneInfo("Europe/Zurich")
UTC = timezone.utc

ALLOWED_USERS = [
    n.strip()
    for n in os.environ.get("ALLOWED_USERS", "Noah,Elena,Timon,Stefan,Gast").split(",")
    if n.strip()
]

DATABASE_URL = os.environ.get("DATABASE_URL") or st.secrets.get("DATABASE_URL")
if not DATABASE_URL:
    st.stop()
    raise RuntimeError("Set DATABASE_URL via environment or Streamlit secrets.")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=5,
    max_overflow=5,
)

# ---------------- DB MODELS ----------------
class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    sessions: Mapped[list["WorkSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    adjustments: Mapped[list["Adjustment"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    logs: Mapped[list["Log"]] = relationship(back_populates="user", cascade="all, delete-orphan")

class WorkSession(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    # Strings (UTC) für Kompatibilität zu bestehender DB
    start_ts: Mapped[str] = mapped_column(String(19), nullable=False)  # "YYYY-MM-DD HH:MM:SS" (UTC)
    end_ts: Mapped[str | None] = mapped_column(String(19), nullable=True)  # (UTC)
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user: Mapped[User] = relationship(back_populates="sessions")

class Adjustment(Base):
    __tablename__ = "adjustments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_ts: Mapped[str] = mapped_column(String(19), nullable=False)  # (UTC)
    user: Mapped[User] = relationship(back_populates="adjustments")

class Log(Base):
    __tablename__ = "logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # start | stop | adjust
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts: Mapped[str] = mapped_column(String(19), nullable=False)  # (UTC)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    user: Mapped[User] = relationship(back_populates="logs")

# Create tables + Indizes
with engine.begin() as conn:
    Base.metadata.create_all(conn)
    # genau eine aktive Session pro User
    conn.exec_driver_sql("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_session_per_user
        ON sessions (user_id)
        WHERE end_ts IS NULL;
    """)
    # sinnvolle Zusatzindizes
    conn.exec_driver_sql("""
        CREATE INDEX IF NOT EXISTS idx_sessions_user_end
        ON sessions (user_id, end_ts);
    """)
    conn.exec_driver_sql("""
        CREATE INDEX IF NOT EXISTS idx_adjustments_user_created
        ON adjustments (user_id, created_ts);
    """)

# ---------------- HELPERS (Zeit) ----------------
def now_utc_str() -> str:
    """UTC-String 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

def utc_str_to_dt(s: str) -> datetime:
    """Parst unseren UTC-String zu tz-aware UTC datetime."""
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)

def to_local_str(utc_s: str) -> str:
    """Zeigt UTC-String als lokale Zeit (Europe/Zurich)."""
    return utc_str_to_dt(utc_s).astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")

def minutes_between_utc_str(start_utc: str, end_utc: str) -> int:
    """Rundet ab 30s auf, sonst ab (niemals <0)."""
    start = utc_str_to_dt(start_utc)
    end = utc_str_to_dt(end_utc)
    total_seconds = int((end - start).total_seconds())
    mins, secs = divmod(total_seconds, 60)
    return max(0, mins + (1 if secs >= 30 else 0))

def seconds_between_utc_str(start_utc: str, end_utc: str) -> int:
    start = utc_str_to_dt(start_utc)
    end = utc_str_to_dt(end_utc)
    return max(0, int((end - start).total_seconds()))

def month_key_local(dt: datetime) -> str:
    """YYYY-MM in lokaler Zeitzone (für Monatsübersicht)."""
    return dt.astimezone(TZ).strftime("%Y-%m")

def fmt_hms(total_seconds: int) -> str:
    td = timedelta(seconds=total_seconds)
    hours = td.days * 24 + td.seconds // 3600
    minutes = (td.seconds % 3600) // 60
    seconds = td.seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def live_timer_html_from_utc_str(start_utc: str):
    """
    Clientseitiger HH:MM:SS-Timer ohne Streamlit-Rerun.
    Füttern mit ISO-String inkl. 'Z' (UTC) für konsistente Browser-Interpretation.
    """
    # zu ISO mit 'Z'
    start_iso_z = utc_str_to_dt(start_utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    html = f"""
    <div id="tt-timer" 
         style="font-size:clamp(1.6rem, 3vw, 2.4rem);
                font-weight:700;
                font-variant-numeric: tabular-nums;
                letter-spacing:0.5px;
                color:#00FFAA;">
      00:00:00
    </div>
    <script>
      const pad = (n) => n.toString().padStart(2,'0');
      const start = new Date("{start_iso_z}"); // UTC
      function tick(){{
        const now = new Date();
        let sec = Math.floor((now - start)/1000);
        if (sec < 0) sec = 0;
        const h = Math.floor(sec/3600);
        const m = Math.floor((sec%3600)/60);
        const s = sec%60;
        const el = document.getElementById('tt-timer');
        if (el) el.textContent = `${{pad(h)}}:${{pad(m)}}:${{pad(s)}}`;
      }}
      tick();
      setInterval(tick, 1000);
    </script>
    """
    st.components.v1.html(html, height=70)

# ---------------- HELPERS (DB) ----------------
def safe_commit(session: Session):
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise

def get_or_create_user(name: str) -> User:
    with Session(engine) as s:
        user = s.scalar(select(User).where(User.name == name))
        if not user:
            user = User(name=name)
            s.add(user)
            safe_commit(s)
            s.refresh(user)
        return user

def active_session(user_id: int) -> WorkSession | None:
    with Session(engine) as s:
        return s.scalar(
            select(WorkSession)
            .where(WorkSession.user_id == user_id, WorkSession.end_ts.is_(None))
            .order_by(WorkSession.id.desc())
        )

def add_log(user_id: int, kind: str, minutes: int | None = None, details: str | None = None):
    with Session(engine) as s:
        s.add(Log(
            user_id=user_id,
            kind=kind,
            minutes=minutes,
            ts=now_utc_str(),
            details=details or ""
        ))
        safe_commit(s)

def month_totals(user_id: int):
    """
    Liefert list[(YYYY-MM, minuten)] aus abgeschlossenen Sessions + Adjustments,
    gruppiert nach Monat in lokaler Zeitzone.
    (Python-Gruppierung für maximale Kompatibilität zu TEXT-Spalten)
    """
    with Session(engine) as s:
        sessions = s.scalars(
            select(WorkSession)
            .where(WorkSession.user_id == user_id, WorkSession.end_ts.is_not(None))
        ).all()
        adjustments = s.scalars(
            select(Adjustment).where(Adjustment.user_id == user_id)
        ).all()

    totals: dict[str, int] = {}

    # Sessions: Endzeit bestimmt Monat
    for row in sessions:
        end_local = utc_str_to_dt(row.end_ts).astimezone(TZ)
        k = month_key_local(end_local)
        totals[k] = totals.get(k, 0) + int(row.minutes or 0)

    # Adjustments: Buchungszeit bestimmt Monat
    for a in adjustments:
        created_local = utc_str_to_dt(a.created_ts).astimezone(TZ)
        k = month_key_local(created_local)
        totals[k] = totals.get(k, 0) + int(a.minutes)

    return sorted(totals.items(), key=lambda kv: kv[0], reverse=True)

def month_minutes(user_id: int) -> int:
    key = datetime.now(TZ).strftime("%Y-%m")
    for k, v in month_totals(user_id):
        if k == key:
            return v
    return 0

# Caching für Übersicht/Log
@st.cache_data(ttl=10, show_spinner=False)
def load_month_totals_cached(user_id: int):
    return month_totals(user_id)

@st.cache_data(ttl=10, show_spinner=False)
def load_logs_cached(user_id: int, limit: int = 500):
    with Session(engine) as s:
        rows = s.execute(
            select(Log.ts, Log.kind, Log.minutes, Log.details)
            .where(Log.user_id == user_id)
            .order_by(Log.id.desc())
            .limit(limit)
        ).all()
    return rows

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="Zeiterfassung", page_icon="⏱️", layout="wide")
st.title("⏱️ Zeiterfassung")

# Sidebar Login
st.sidebar.header("Login")
name = st.sidebar.selectbox("Name", ALLOWED_USERS, index=0, key="name_select")

col_login = st.sidebar.columns(2)
if col_login[0].button("Einloggen", help="Logge dich mit deinem Namen ein"):
    user_obj = get_or_create_user(name)
    st.session_state["user"] = {"id": user_obj.id, "name": user_obj.name}
    st.success(f"Hallo {user_obj.name}!")
    st.rerun()

if col_login[1].button("Logout", help="Beendet die Sitzung"):
    st.session_state.pop("user", None)
    st.rerun()

user = st.session_state.get("user")
if not user:
    st.info("Bitte links deinen Namen wählen und **Einloggen**.")
    st.stop()

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader(f"Hallo {user['name']} 👋")

    s_active = active_session(user["id"])
    live_box = st.empty()

    if s_active:
        # Start in lokaler Anzeige
        st.caption(f"Läuft seit: {to_local_str(s_active.start_ts)}")

        # Live-Timer (kein Rerun)
        with live_box:
            st.markdown("**Laufzeit (live):**")
            live_timer_html_from_utc_str(s_active.start_ts)

        if st.button("⏹️ Stoppen", type="primary", help="Beende deine Zeiterfassung"):
            try:
                end_ts = now_utc_str()
                secs = seconds_between_utc_str(s_active.start_ts, end_ts)
                mins = minutes_between_utc_str(s_active.start_ts, end_ts)
                mins = max(1, mins)  # nie 0 Minuten verbuchen

                with Session(engine) as s:
                    obj = s.get(WorkSession, s_active.id)
                    obj.end_ts = end_ts
                    obj.minutes = mins
                    safe_commit(s)

                add_log(user["id"], "stop", minutes=mins,
                        details=f"Stop um {to_local_str(end_ts)} (+{fmt_hms(secs)})")
                st.success(f"Gestoppt: {fmt_hms(secs)} verbucht.")
            except SQLAlchemyError:
                st.error("Konnte Session nicht beenden. Bitte erneut versuchen.")
                raise
            st.rerun()
    else:
        if st.button("▶️ Starten", type="primary", help="Starte eine neue Zeiterfassung"):
            try:
                ts = now_utc_str()
                with Session(engine) as s:
                    s.add(WorkSession(user_id=user["id"], start_ts=ts))
                    safe_commit(s)
                add_log(user["id"], "start", details=f"Start um {to_local_str(ts)}")
                st.success("Zeiterfassung gestartet.")
            except IntegrityError:
                st.info("Es läuft bereits eine Session.")
            except SQLAlchemyError:
                st.error("Konnte die Session nicht starten. Bitte erneut versuchen.")
                raise
            st.rerun()

    st.divider()
    st.subheader("Manuelle Anpassung")
    delta = st.number_input("±Minuten (z. B. -30 oder 30)", step=1, value=0)
    reason = st.text_input("Kommentar (optional)", value="")
    if st.button("Buchen", help="Manuell Zeit hinzufügen oder abziehen"):
        if delta == 0:
            st.warning("Bitte eine von 0 verschiedene Minutenanzahl eingeben.")
        else:
            try:
                ts = now_utc_str()
                with Session(engine) as s:
                    s.add(Adjustment(user_id=user["id"], minutes=int(delta),
                                     reason=reason.strip(), created_ts=ts))
                    safe_commit(s)
                add_log(user["id"], "adjust", minutes=int(delta),
                        details=reason or "Manuelle Anpassung")
                st.success(f"{'+' if delta>0 else ''}{int(delta)} Minuten verbucht.")
            except SQLAlchemyError:
                st.error("Konnte die Anpassung nicht speichern.")
                raise
            st.rerun()

with col2:
    st.subheader("Monatsübersicht")
    current = month_minutes(user["id"])
    st.metric("Aktueller Monat", f"{current//60:02d}:{current%60:02d} h", help=f"{current} Minuten (gerundet)")

    data = load_month_totals_cached(user["id"])
    df = pd.DataFrame([{"Monat": k, "Minuten": m, "Stunden": round(m/60, 2)} for k, m in data])
    st.dataframe(df, use_container_width=True)
    if not df.empty:
        st.download_button(
            "CSV: Monate",
            df.to_csv(index=False).encode("utf-8"),
            file_name=f"months_{user['name']}.csv",
            mime="text/csv"
        )

st.divider()
st.subheader("Logbuch")
rows = load_logs_cached(user["id"], limit=500)
df_log = pd.DataFrame(rows, columns=["ts", "kind", "minutes", "details"])
# Log-Zeit in lokaler Anzeige (nicht gererundet); wir lassen UTC-Strings wie sie sind,
# optional kannst du die Spalte konvertieren:
if not df_log.empty:
    try:
        df_log["ts_local"] = df_log["ts"].apply(to_local_str)
        df_log = df_log[["ts_local", "kind", "minutes", "details"]].rename(columns={"ts_local": "ts"})
    except Exception:
        pass

st.dataframe(df_log, use_container_width=True)
if not df_log.empty:
    st.download_button(
        "CSV: Logbuch",
        df_log.to_csv(index=False).encode("utf-8"),
        file_name=f"logs_{user['name']}.csv",
        mime="text/csv"
    )
