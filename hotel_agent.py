import json
import os
import re
import logging
from typing import Any, Optional

from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
)
from livekit.agents.llm import function_tool
from livekit.plugins import openai, silero

from wp_front_ajax_client import FrontAjaxClient

load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("medical-agent-sip")

CLINIC_NAME = os.environ.get("CLINIC_NAME", "Prywatna Praktyka Ortopedyczna Doktora Adama Kowalskiego")
DOCTOR_ID = os.environ.get("DEMO_DOCTOR_ID", "")
REALTIME_VOICE = os.environ.get("REALTIME_VOICE", "shimmer")
AGENT_NAME = os.environ.get("AGENT_NAME", "lka")
FRONT_AJAX_URL = os.environ.get("FRONT_AJAX_URL", "")
FRONT_AJAX_API_KEY_SET = bool(os.environ.get("FRONT_AJAX_API_KEY") or os.environ.get("SMS_API_KEY"))

print("=== LKA KOWALSKI DEMO BUILD v1 LOGS ===")


def get_caller_phone_from_room_name(room_name: str) -> Optional[str]:
    if not room_name:
        return None
    match = re.search(r"\+?\d{6,}", room_name)
    return match.group(0) if match else None


def _safe_dump(value: Any, limit: int = 2500) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = repr(value)
    if len(text) > limit:
        return text[:limit] + "...<cut>"
    return text


class KowalskiDemoAgent(Agent):
    def __init__(
        self,
        caller_phone: Optional[str],
        client: FrontAjaxClient,
        recognized_patient: Optional[dict[str, Any]] = None,
    ) -> None:
        self.caller_phone = caller_phone
        self.client = client
        self.recognized_patient = recognized_patient or {}
        self.last_presented_slots: list[dict[str, Any]] = []

        patient_line = "Nie rozpoznano pacjenta po numerze telefonu."
        if self.recognized_patient.get("pcj_id"):
            patient_line = (
                f"Rozpoznany pacjent po numerze telefonu: "
                f"{self.recognized_patient.get('first_name', '')} {self.recognized_patient.get('last_name', '')}, "
                f"pcj_id={self.recognized_patient.get('pcj_id', '')}, telefon={self.recognized_patient.get('phone', '')}."
            )

        logger.info("[Agent] INIT caller_phone=%s", caller_phone)
        logger.info("[Agent] INIT recognized_patient=%s", _safe_dump(self.recognized_patient))

        super().__init__(
            instructions=f"""
# Rola i cel
Jestes medycznym asystentem glosowym dla placowki {CLINIC_NAME}. Obslugujesz jedna praktyke doktora Adama Kowalskiego.

# Najwazniejsze zasady
- Mow po polsku, krotko, naturalnie i spokojnie.
- Jedna mysl na raz.
- Nie zostawiaj rozmowcy w ciszy. Gdy wywolujesz narzedzie, powiedz najpierw krotko: "Juz sprawdzam..." albo "Chwileczke...".
- Nie wymyslaj terminow, nazw uslug, identyfikatorow ani statusow. Korzystaj tylko z danych z narzedzi.
- Gdy masz kilka terminow, podaj maksymalnie 3 naraz.
- Nie pytaj ponownie o rzeczy, ktore juz zostaly ustalone w rozmowie.
- Nie pytaj o numer telefonu, jesli mamy numer z polaczenia i pacjent go potwierdza.
- Jesli pacjent jest rozpoznany po numerze telefonu, nie pytaj o imie i nazwisko do zwyklego odwolania lub przelozenia, chyba ze pojawi sie niejednoznacznosc.
- Przed umowieniem wizyty musisz potwierdzic: termin, imie i nazwisko pacjenta oraz numer telefonu.
- Przed odwolaniem lub przeniesieniem musisz potwierdzic, ktora konkretna wizyte pacjent ma na mysli.

# Kontekst
Numer z polaczenia: {caller_phone or 'nieznany'}
{patient_line}
Doktor: Adam Kowalski
Specjalizacja: ortopedia

# Dozwolone sprawy
- sprawdzenie terminow
- umowienie wizyty
- odwolanie wizyty
- przeniesienie wizyty

# Gdy pacjent pyta o terminy
1. Ustal brakujace informacje, zwlaszcza od kiedy szukac.
2. Wywolaj narzedzie sprawdz_terminy.
3. Podaj 2-3 najblizsze pasujace opcje glosowo.

# Gdy pacjent chce umowic wizyte
1. Ustal termin.
2. Jesli pacjent jest rozpoznany po numerze telefonu, wykorzystaj te dane.
3. Jesli nie jest rozpoznany, zbierz tylko imie i nazwisko; numer telefonu wez z polaczenia, chyba ze pacjent chce inny.
4. Potwierdz dane.
5. Wywolaj umow_termin.

# Gdy pacjent chce odwolac lub przeniesc wizyte
1. Jesli pacjent jest rozpoznany po numerze telefonu, najpierw sprobuj znalezc jego wizyte narzedziem.
2. Potwierdz konkretna wizyte.
3. Dopiero potem odwolaj lub przenies.

# Powitanie
Na poczatku przywitaj sie krotko i zapytaj, w czym mozesz pomoc.
""",
        )

    @function_tool()
    async def sprawdz_terminy(
        self,
        context: RunContext,
        data_od: str,
        data_do: str = "",
        pora_dnia: str = "",
    ) -> dict[str, Any]:
        """Sprawdza wolne terminy doktora Adama Kowalskiego.

        Args:
            data_od: Data poczatkowa wyszukiwania w formacie YYYY-MM-DD.
            data_do: Opcjonalna data koncowa w formacie YYYY-MM-DD.
            pora_dnia: Opcjonalnie rano, popoludnie albo wieczor.
        """
        logger.info("[Tool] sprawdz_terminy START data_od=%s data_do=%s pora_dnia=%s", data_od, data_do, pora_dnia)
        if not DOCTOR_ID:
            logger.error("[Tool] sprawdz_terminy ERROR DEMO_DOCTOR_ID missing")
            return {"ok": False, "error": "DEMO_DOCTOR_ID missing"}

        raw = await self.client.free_terms(doctor_id=DOCTOR_ID, date_from=data_od)
        logger.info("[Tool] sprawdz_terminy raw=%s", _safe_dump(raw))

        if not raw.get("ok"):
            logger.error("[Tool] sprawdz_terminy ERROR raw not ok")
            return raw

        slots = self.client.compact_slots(raw.get("data"), date_to=data_do, time_of_day=pora_dnia)
        self.last_presented_slots = slots[:10]

        result = {
            "ok": True,
            "doctor_id": DOCTOR_ID,
            "slots": slots[:3],
            "slots_total": len(slots),
        }
        logger.info("[Tool] sprawdz_terminy normalized=%s", _safe_dump(result))
        return result

    @function_tool()
    async def umow_termin(
        self,
        context: RunContext,
        data_wizyty: str,
        godzina_od: str,
        imie: str = "",
        nazwisko: str = "",
        telefon: str = "",
    ) -> dict[str, Any]:
        """Umawia wizyte u doktora Adama Kowalskiego.

        Args:
            data_wizyty: Data wizyty w formacie YYYY-MM-DD.
            godzina_od: Godzina rozpoczecia w formacie HH:MM.
            imie: Imie pacjenta. Mozna pominac, jesli pacjent zostal rozpoznany po numerze.
            nazwisko: Nazwisko pacjenta. Mozna pominac, jesli pacjent zostal rozpoznany po numerze.
            telefon: Numer telefonu pacjenta. Opcjonalny, domyslnie numer z polaczenia.
        """
        logger.info(
            "[Tool] umow_termin START data_wizyty=%s godzina_od=%s imie=%s nazwisko=%s telefon=%s",
            data_wizyty,
            godzina_od,
            imie,
            nazwisko,
            telefon,
        )

        phone_to_use = telefon or self.caller_phone or self.recognized_patient.get("phone", "")
        logger.info("[Tool] umow_termin phone_to_use=%s", phone_to_use)
        logger.info("[Tool] umow_termin recognized_patient BEFORE=%s", _safe_dump(self.recognized_patient))

        patient = self.recognized_patient if self.recognized_patient.get("pcj_id") else None
        logger.info("[Tool] umow_termin patient from cache=%s", _safe_dump(patient))

        if not patient:
            resolved = await self.client.patient_resolve_or_create(
                caller_phone=phone_to_use,
                first_name=imie,
                last_name=nazwisko,
            )
            logger.info("[Tool] umow_termin patient_resolve_or_create raw=%s", _safe_dump(resolved))

            if not resolved.get("ok"):
                logger.error("[Tool] umow_termin ERROR patient_not_resolved")
                return {
                    "ok": False,
                    "error": "patient_not_resolved",
                    "details": resolved,
                }

            patient = self.client.compact_patient(resolved.get("data"))
            self.recognized_patient = patient
            logger.info("[Tool] umow_termin compact patient=%s", _safe_dump(patient))
            logger.info("[Tool] umow_termin recognized_patient AFTER=%s", _safe_dump(self.recognized_patient))

        slots_source = self.last_presented_slots
        logger.info("[Tool] umow_termin last_presented_slots=%s", _safe_dump(slots_source))

        if not slots_source:
            logger.info("[Tool] umow_termin no cached slots, fetching again")
            raw_slots = await self.client.free_terms(doctor_id=DOCTOR_ID, date_from=data_wizyty)
            logger.info("[Tool] umow_termin raw_slots=%s", _safe_dump(raw_slots))

            if not raw_slots.get("ok"):
                logger.error("[Tool] umow_termin ERROR raw_slots not ok")
                return raw_slots

            slots_source = self.client.compact_slots(raw_slots.get("data"), date_to=data_wizyty)
            logger.info("[Tool] umow_termin normalized slots_source=%s", _safe_dump(slots_source))

        slot = self.client.choose_slot(slots_source, appointment_date=data_wizyty, appointment_time=godzina_od)
        logger.info("[Tool] umow_termin chosen slot=%s", _safe_dump(slot))

        if not slot:
            logger.error("[Tool] umow_termin ERROR slot_not_found")
            return {"ok": False, "error": "slot_not_found"}

        booked = await self.client.appointment_book(
            doctor_id=DOCTOR_ID,
            start_dt=slot["start"],
            end_dt=slot["end"],
            patient_id=patient.get("pcj_id", ""),
        )
        logger.info("[Tool] umow_termin appointment_book raw=%s", _safe_dump(booked))

        result = {
            "ok": booked.get("ok", False),
            "booking_result": booked,
            "patient": patient,
            "slot": slot,
        }
        logger.info("[Tool] umow_termin result=%s", _safe_dump(result))
        return result

    @function_tool()
    async def odwolaj_termin(
        self,
        context: RunContext,
        data_wizyty: str = "",
        pw_id: str = "",
    ) -> dict[str, Any]:
        """Odwoluje istniejaca wizyte pacjenta.

        Args:
            data_wizyty: Opcjonalna data wizyty do anulowania w formacie YYYY-MM-DD.
            pw_id: Opcjonalny identyfikator wizyty, jesli jest juz znany.
        """
        logger.info("[Tool] odwolaj_termin START data_wizyty=%s pw_id=%s", data_wizyty, pw_id)

        patient_id = self.recognized_patient.get("pcj_id", "")
        logger.info("[Tool] odwolaj_termin recognized_patient=%s", _safe_dump(self.recognized_patient))
        logger.info("[Tool] odwolaj_termin patient_id=%s caller_phone=%s", patient_id, self.caller_phone)

        lookup = await self.client.appointment_lookup(
            patient_id=patient_id or None,
            phone=self.caller_phone,
            doctor_id=DOCTOR_ID,
        )
        logger.info("[Tool] odwolaj_termin appointment_lookup raw=%s", _safe_dump(lookup))

        if not lookup.get("ok"):
            logger.error("[Tool] odwolaj_termin ERROR lookup not ok")
            return lookup

        visit = self.client.choose_visit(lookup.get("data"), pw_id=pw_id, appointment_date=data_wizyty)
        logger.info("[Tool] odwolaj_termin chosen visit=%s", _safe_dump(visit))

        if not visit:
            logger.error("[Tool] odwolaj_termin ERROR appointment_not_found")
            return {"ok": False, "error": "appointment_not_found", "lookup": lookup}

        cancel = await self.client.appointment_cancel(pw_id=str(visit.get("pw_id", "")))
        logger.info("[Tool] odwolaj_termin appointment_cancel raw=%s", _safe_dump(cancel))

        result = {
            "ok": cancel.get("ok", False),
            "cancel_result": cancel,
            "visit": visit,
        }
        logger.info("[Tool] odwolaj_termin result=%s", _safe_dump(result))
        return result

    @function_tool()
    async def przenies_termin(
        self,
        context: RunContext,
        stara_data_wizyty: str,
        nowa_data_wizyty: str,
        nowa_godzina_od: str,
        pw_id: str = "",
    ) -> dict[str, Any]:
        """Przenosi wizyte pacjenta na inny termin.

        Args:
            stara_data_wizyty: Data obecnej wizyty w formacie YYYY-MM-DD.
            nowa_data_wizyty: Nowa data wizyty w formacie YYYY-MM-DD.
            nowa_godzina_od: Nowa godzina rozpoczecia w formacie HH:MM.
            pw_id: Opcjonalny identyfikator wizyty.
        """
        logger.info(
            "[Tool] przenies_termin START stara_data_wizyty=%s nowa_data_wizyty=%s nowa_godzina_od=%s pw_id=%s",
            stara_data_wizyty,
            nowa_data_wizyty,
            nowa_godzina_od,
            pw_id,
        )

        patient_id = self.recognized_patient.get("pcj_id", "")
        logger.info("[Tool] przenies_termin recognized_patient=%s", _safe_dump(self.recognized_patient))
        logger.info("[Tool] przenies_termin patient_id=%s caller_phone=%s", patient_id, self.caller_phone)

        lookup = await self.client.appointment_lookup(
            patient_id=patient_id or None,
            phone=self.caller_phone,
            doctor_id=DOCTOR_ID,
        )
        logger.info("[Tool] przenies_termin appointment_lookup raw=%s", _safe_dump(lookup))

        if not lookup.get("ok"):
            logger.error("[Tool] przenies_termin ERROR lookup not ok")
            return lookup

        visit = self.client.choose_visit(lookup.get("data"), pw_id=pw_id, appointment_date=stara_data_wizyty)
        logger.info("[Tool] przenies_termin chosen old visit=%s", _safe_dump(visit))

        if not visit:
            logger.error("[Tool] przenies_termin ERROR appointment_not_found")
            return {"ok": False, "error": "appointment_not_found", "lookup": lookup}

        raw_slots = await self.client.free_terms(doctor_id=DOCTOR_ID, date_from=nowa_data_wizyty)
        logger.info("[Tool] przenies_termin raw_slots=%s", _safe_dump(raw_slots))

        if not raw_slots.get("ok"):
            logger.error("[Tool] przenies_termin ERROR raw_slots not ok")
            return raw_slots

        slots = self.client.compact_slots(raw_slots.get("data"), date_to=nowa_data_wizyty)
        logger.info("[Tool] przenies_termin normalized slots=%s", _safe_dump(slots))

        slot = self.client.choose_slot(slots, appointment_date=nowa_data_wizyty, appointment_time=nowa_godzina_od)
        logger.info("[Tool] przenies_termin chosen new slot=%s", _safe_dump(slot))

        if not slot:
            logger.error("[Tool] przenies_termin ERROR new_slot_not_found")
            return {"ok": False, "error": "new_slot_not_found", "slots": slots[:5]}

        moved = await self.client.appointment_reschedule(
            pw_id=str(visit.get("pw_id", "")),
            new_start=slot["start"],
            new_end=slot["end"],
            doctor_id=DOCTOR_ID,
        )
        logger.info("[Tool] przenies_termin appointment_reschedule raw=%s", _safe_dump(moved))

        result = {
            "ok": moved.get("ok", False),
            "reschedule_result": moved,
            "old_visit": visit,
            "new_slot": slot,
        }
        logger.info("[Tool] przenies_termin result=%s", _safe_dump(result))
        return result


async def entrypoint(ctx: JobContext):
    logger.info("=" * 50)
    logger.info("[Agent] New SIP call! Room: %s", ctx.room.name)
    logger.info("[Agent] FRONT_AJAX_URL: %s", FRONT_AJAX_URL)
    logger.info("[Agent] FRONT_AJAX_API_KEY set: %s", FRONT_AJAX_API_KEY_SET)
    logger.info("[Agent] DEMO_DOCTOR_ID: %s", DOCTOR_ID)
    logger.info("=" * 50)

    caller_phone = get_caller_phone_from_room_name(ctx.room.name)
    logger.info("[Agent] Caller phone (from room name): %s", caller_phone)

    client = FrontAjaxClient()
    logger.info("[Agent] FrontAjaxClient enabled=%s", client.enabled)

    recognized_patient: dict[str, Any] = {}
    if caller_phone:
        logger.info("[Agent] Trying patient_get by caller phone=%s", caller_phone)
        resolved = await client.patient_get(phone=caller_phone)
        logger.info("[Agent] patient_get raw=%s", _safe_dump(resolved))

        if resolved.get("ok") and resolved.get("data"):
            recognized_patient = client.compact_patient(resolved.get("data"))
            logger.info("[Agent] Recognized patient compact=%s", _safe_dump(recognized_patient))
        else:
            logger.info("[Agent] Patient not recognized by caller phone")

    agent = KowalskiDemoAgent(
        caller_phone=caller_phone,
        client=client,
        recognized_patient=recognized_patient,
    )

    # Mechanika komunikacyjna zostawiona jak w dzialajacym pliku.
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice=REALTIME_VOICE),
        vad=silero.VAD.load(),
    )

    await session.start(
        agent=agent,
        room=ctx.room,
    )

    await ctx.connect()

    logger.info("[Agent] Session started, generating greeting...")

    await session.generate_reply(
        instructions="Przywitaj sie dokladnie tymi slowami: Dzien dobry, tu medyczny asystent glosowy. W czym moge pomoc?"
    )

    logger.info("[Agent] Greeting generated.")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=AGENT_NAME,
        )
    )
