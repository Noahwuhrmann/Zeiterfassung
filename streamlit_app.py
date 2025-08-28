import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from sqlalchemy import (
    create_engine, Integer, String, Text, ForeignKey,
    select, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, relationship

# ---------------- CONFIG ----------------
TZ = ZoneInfo("Europe/Zurich")
ALLOWED_USERS = [n.strip() for n in os.environ.get("ALLOWED_USERS", "Noah,Elena,Timon,Stefan,Gast").split(",") if n.strip()]
DATABASE_URL = os.environ.get("DATABASE_URL") or st.secrets.get("DATABASE_URL")

if not DATABASE_URL:
    st.stop()
    raise RuntimeError("Set DATABASE_URL via environment or Streamlit secrets.")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

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
    start_ts: Mapped[str] = mapped_column(String(19), nullable=False)  # "YYYY-MM-DD HH:MM:SS"
    end_ts: Mapped[str | None] = mapped_column(String(19), nullable=True)
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user: Mapped[User] = relationship(back_populates="sessions")

class Adjustment(Base):
    __tablename__ = "adjustments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_ts: Mapped[str] = mapped_column(String(19), nullable=False)
    user: Mapped[User] = relationship(back_populates="adjustments")

class Log(Base):
    __tablename__ = "logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # start | stop | adjust
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts: Mapped[str] = mapped_column(String(19), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    user: Mapped[User] = relationship(back_populates="logs")

# Create tables if not exist
with engine.begin() as conn:
    Base.metadata.create_all(conn)

# ---------------- HELPERS ----------------
def now_local() -> datetime:
    return datetime.now(TZ)

def minutes_between(start_iso: str, end_iso: str) -> int:
    start = datetime.fromisoformat(start_iso).replace(tzinfo=TZ)
    end = datetime.fromisoformat(end_iso).replace(tzinfo=TZ)
    delta = end - start
    return max(0, int(delta.total_seconds() // 60))

def seconds_between(start_iso: str, end_iso: str) -> int:
    start = datetime.fromisoformat(start_iso).replace(tzinfo=TZ)
    end = datetime.fromisoformat(end_iso).replace(tzinfo=TZ)
    return max(0, int((end - start).total_seconds()))

def month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")

def get_or_create_user(name: str) -> User:
    with Session(engine) as s:
        user = s.scalar(select(User).where(User.name == name))
        if not user:
            user = User(name=name)
            s.add(user)
            s.commit()
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
            ts=now_local().strftime("%Y-%m-%d %H:%M:%S"),
            details=details or ""
        ))
        s.commit()

def month_totals(user_id: int):
    """Return list[(YYYY-MM, minutes)] from finished sessions + adjustments grouped by month."""
    with Session(engine) as s:
        sessions = s.scalars(
            select(WorkSession)
            .where(WorkSession.user_id == user_id, WorkSession.end_ts.is_not(None))
        ).all()
        adjustments = s.scalars(
            select(Adjustment).where(Adjustment.user_id == user_id)
        ).all()
    totals: dict[str, int] = {}
    for row in sessions:
        end = datetime.fromisoformat(row.end_ts).replace(tzinfo=TZ)
        k = month_key(end)
        totals[k] = totals.get(k, 0) + int(row.minutes or 0)
    for a in adjustments:
        ts = datetime.fromisoformat(a.created_ts).replace(tzinfo=TZ)
        k = month_key(ts)
        totals[k] = totals.get(k, 0) + int(a.minutes)
    return sorted(totals.items(), key=lambda kv: kv[0], reverse=True)

def month_minutes(user_id: int) -> int:
    key = month_key(now_local())
    for k, v in month_totals(user_id):
        if k == key:
            return v
    return 0

def fmt_hms(total_seconds: int) -> str:
    td = timedelta(seconds=total_seconds)
    hours = td.days * 24 + td.seconds // 3600
    minutes = (td.seconds % 3600) // 60
    seconds = td.seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def live_timer_html(start_iso: str):
    """
    Clientseitiger HH:MM:SS-Timer ohne Streamlit-Rerun.
    Erwartet 'YYYY-MM-DD HH:MM:SS' in *Browser-Lokaltzeit*.
    """
    start_js = start_iso.replace(" ", "T")  # "YYYY-MM-DDTHH:MM:SS"
    html = f"""
    <div id="tt-timer" 
         style="font-size:2rem;
                font-weight:600;
                font-variant-numeric: tabular-nums;
                color:#00FFAA;  /* helle, gut sichtbare Farbe */">
      00:00:00
    </div>
    <script>
      const pad = (n) => n.toString().padStart(2,'0');
      const start = new Date("{start_js}");
      function tick(){{
        const now = new Date();
        let sec = Math.floor((now - start)/1000);
        if (sec < 0) sec = 0;
        const h = Math.floor(sec/3600);
        const m = Math.floor((sec%3600)/60);
        const s = sec%60;
        document.getElementById('tt-timer').textContent = `${{pad(h)}}:${{pad(m)}}:${{pad(s)}}`;
      }}
      tick();
      setInterval(tick, 1000);
    </script>
    """
    st.components.v1.html(html, height=70)

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="Zeiterfassung", page_icon="⏱️", layout="wide")
st.title("⏱️ Zeiterfassung")

# Sidebar Login
st.sidebar.header("Login")
name = st.sidebar.selectbox("Name (vordefiniert)", ALLOWED_USERS, index=0, key="name_select")
if st.sidebar.button("Einloggen"):
    user_obj = get_or_create_user(name)
    st.session_state["user"] = {"id": user_obj.id, "name": user_obj.name}
    st.success(f"Hallo {user_obj.name}!")

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
        st.caption(f"Läuft seit: {s_active.start_ts}")

        # Live-Timer clientseitig (kein Rerun)
        with live_box:
            st.markdown("**Laufzeit (live):**")
            live_timer_html(s_active.start_ts)

        if st.button("⏹️ Stoppen", type="primary"):
            end_ts = now_local().strftime("%Y-%m-%d %H:%M:%S")
            secs = seconds_between(s_active.start_ts, end_ts)
            mins = minutes_between(s_active.start_ts, end_ts)
            mins = max(1, mins)  # nie 0 Minuten verbuchen

            with Session(engine) as s:
                obj = s.get(WorkSession, s_active.id)
                obj.end_ts = end_ts
                obj.minutes = mins
                s.commit()

            add_log(user["id"], "stop", minutes=mins, details=f"Stop um {end_ts} (+{fmt_hms(secs)})")
            st.success(f"Gestoppt: {fmt_hms(secs)} verbucht.")
            st.experimental_rerun()  # einmalig neu zeichnen, damit Übersicht/Log aktualisiert sind
    else:
        if st.button("▶️ Starten", type="primary"):
            ts = now_local().strftime("%Y-%m-%d %H:%M:%S")
            with Session(engine) as s:
                s.add(WorkSession(user_id=user["id"], start_ts=ts))
                s.commit()
            add_log(user["id"], "start", details=f"Start um {ts}")
            st.success("Zeiterfassung gestartet.")
            st.experimental_rerun()  # einmalig neu zeichnen, damit Timer erscheint

    st.divider()
    st.subheader("Manuelle Anpassung")
    delta = st.number_input("±Minuten (z. B. -30 oder 30)", step=1, value=0)
    reason = st.text_input("Kommentar (optional)", value="")
    if st.button("Buchen"):
        if delta == 0:
            st.warning("Bitte eine von 0 verschiedene Minutenanzahl eingeben.")
        else:
            ts = now_local().strftime("%Y-%m-%d %H:%M:%S")
            with Session(engine) as s:
                s.add(Adjustment(user_id=user["id"], minutes=int(delta), reason=reason.strip(), created_ts=ts))
                s.commit()
            add_log(user["id"], "adjust", minutes=int(delta), details=reason or "Manuelle Anpassung")
            st.success(f"{'+' if delta>0 else ''}{int(delta)} Minuten verbucht.")
            st.experimental_rerun()  # Übersicht/Log sofort aktualisieren

with col2:
    st.subheader("Monatsübersicht")
    current = month_minutes(user["id"])
    # hier HH:MM (keine Sekunden, Monatsaggregate)
    st.metric("Aktueller Monat", f"{current//60:02d}:{current%60:02d} h")

    data = month_totals(user["id"])
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
with Session(engine) as s:
    logs = s.execute(
        select(Log.ts, Log.kind, Log.minutes, Log.details)
        .where(Log.user_id == user["id"])
        .order_by(Log.id.desc())
        .limit(500)
    ).all()
df_log = pd.DataFrame(logs, columns=["ts", "kind", "minutes", "details"])
st.dataframe(df_log, use_container_width=True)
if not df_log.empty:
    st.download_button(
        "CSV: Logbuch",
        df_log.to_csv(index=False).encode("utf-8"),
        file_name=f"logs_{user['name']}.csv",
        mime="text/csv"
    )
