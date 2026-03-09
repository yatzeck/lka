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

print("=== LKA KOWALSKI DEMO BUILD v1 ===")


def get_caller_phone_from_room_name(room_name: str) -> Optional[str]:
    if not room_name:
        return None
    match = re.search(r"\+?\d{6,}", room_name)
    return match.group(0) if match else None


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
        logger.info("[Tool] sprawdz_terminy %s %s %s", data_od, data_do, pora_dnia)
        if not DOCTOR_ID:
            return {"ok": False, "error": "DEMO_DOCTOR_ID missing"}
        raw = await self.client.free_terms(doctor_id=DOCTOR_ID, date_from=data_od)
        if not raw.get("ok"):
            return raw
        slots = self.client.compact_slots(raw.get("data"), date_to=data_do, time_of_day=pora_dnia)
        self.last_presented_slots = slots[:10]
        return {
            "ok": True,
            "doctor_id": DOCTOR_ID,
            "slots": slots[:3],
            "slots_total": len(slots),
        }

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
        logger.info("[Tool] umow_termin %s %s", data_wizyty, godzina_od)
        phone_to_use = telefon or self.caller_phone or self.recognized_patient.get("phone", "")
        patient = self.recognized_patient if self.recognized_patient.get("pcj_id") else None

        if not patient:
            resolved = await self.client.patient_resolve_or_create(
                caller_phone=phone_to_use,
                first_name=imie,
                last_name=nazwisko,
            )
            if not resolved.get("ok"):
                return {
                    "ok": False,
                    "error": "patient_not_resolved",
                    "details": resolved,
                }
            patient = self.client.compact_patient(resolved.get("data"))
            self.recognized_patient = patient

        slots_source = self.last_presented_slots
        if not slots_source:
            raw_slots = await self.client.free_terms(doctor_id=DOCTOR_ID, date_from=data_wizyty)
            if not raw_slots.get("ok"):
                return raw_slots
            slots_source = self.client.compact_slots(raw_slots.get("data"), date_to=data_wizyty)

        slot = self.client.choose_slot(slots_source, appointment_date=data_wizyty, appointment_time=godzina_od)
        if not slot:
            return {"ok": False, "error": "slot_not_found"}

        booked = await self.client.appointment_book(
            doctor_id=DOCTOR_ID,
            start_dt=slot["start"],
            end_dt=slot["end"],
            patient_id=patient.get("pcj_id", ""),
        )
        return {
            "ok": booked.get("ok", False),
            "booking_result": booked,
            "patient": patient,
            "slot": slot,
        }

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
        logger.info("[Tool] odwolaj_termin %s %s", data_wizyty, pw_id)
        patient_id = self.recognized_patient.get("pcj_id", "")
        lookup = await self.client.appointment_lookup(
            patient_id=patient_id or None,
            phone=self.caller_phone,
            doctor_id=DOCTOR_ID,
        )
        if not lookup.get("ok"):
            return lookup
        visit = self.client.choose_visit(lookup.get("data"), pw_id=pw_id, appointment_date=data_wizyty)
        if not visit:
            return {"ok": False, "error": "appointment_not_found", "lookup": lookup}
        cancel = await self.client.appointment_cancel(pw_id=str(visit.get("pw_id", "")))
        return {
            "ok": cancel.get("ok", False),
            "cancel_result": cancel,
            "visit": visit,
        }

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
        logger.info("[Tool] przenies_termin %s -> %s %s", stara_data_wizyty, nowa_data_wizyty, nowa_godzina_od)
        patient_id = self.recognized_patient.get("pcj_id", "")
        lookup = await self.client.appointment_lookup(
            patient_id=patient_id or None,
            phone=self.caller_phone,
            doctor_id=DOCTOR_ID,
        )
        if not lookup.get("ok"):
            return lookup
        visit = self.client.choose_visit(lookup.get("data"), pw_id=pw_id, appointment_date=stara_data_wizyty)
        if not visit:
            return {"ok": False, "error": "appointment_not_found", "lookup": lookup}

        raw_slots = await self.client.free_terms(doctor_id=DOCTOR_ID, date_from=nowa_data_wizyty)
        if not raw_slots.get("ok"):
            return raw_slots
        slots = self.client.compact_slots(raw_slots.get("data"), date_to=nowa_data_wizyty)
        slot = self.client.choose_slot(slots, appointment_date=nowa_data_wizyty, appointment_time=nowa_godzina_od)
        if not slot:
            return {"ok": False, "error": "new_slot_not_found", "slots": slots[:5]}

        moved = await self.client.appointment_reschedule(
            pw_id=str(visit.get("pw_id", "")),
            new_start=slot["start"],
            new_end=slot["end"],
            doctor_id=DOCTOR_ID,
        )
        return {
            "ok": moved.get("ok", False),
            "reschedule_result": moved,
            "old_visit": visit,
            "new_slot": slot,
        }


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
    recognized_patient: dict[str, Any] = {}
    if caller_phone:
        resolved = await client.patient_get(phone=caller_phone)
        if resolved.get("ok") and resolved.get("data"):
            recognized_patient = client.compact_patient(resolved.get("data"))
            logger.info("[Agent] Recognized patient: %s", recognized_patient)
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


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=AGENT_NAME,
        )
    )
