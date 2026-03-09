import os
import re
import logging
from typing import Optional

from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.plugins import openai, silero

load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("medical-agent-sip")

print("=== LKA MEDICAL CLEAN BUILD ===")

CLINIC_NAME = os.environ.get("CLINIC_NAME", "Prywatna placówka medyczna")
REALTIME_VOICE = os.environ.get("REALTIME_VOICE", "shimmer")


class MedicalAssistantSIP(Agent):
    def __init__(self, caller_phone: Optional[str] = None) -> None:
        self.caller_phone = caller_phone
        logger.info(f"[Agent] Initialized with caller_phone: {caller_phone}")

        super().__init__(
            instructions=f"""
# Rola i cel
Jestes medycznym asystentem glosowym dla placowki {CLINIC_NAME}. Odbierasz polaczenia telefoniczne i prowadzisz krotka, naturalna rozmowe z pacjentem.

# Osobowosc i ton
## Osobowosc
Spokojna, ciepla, empatyczna i profesjonalna. Sluchasz uwaznie i odpowiadasz rzeczowo.

## Ton
Cieplo-serdeczny, opanowany, pewny siebie. Nigdy nie brzmisz sztucznie ani przesadnie formalnie.

## Dlugosc wypowiedzi
Maksymalnie 2-3 zdania na raz. Mow zwiezle.

## Tempo
Mow spokojnie i wyraznie, bez pospieszania.

## Jezyk
Tylko polski. Nie uzywaj emoji, gwiazdek ani formatowania.

# NAJWAZNIEJSZA ZASADA - ZERO CISZY
Nigdy nie zostawiaj rozmowcy w ciszy. Jesli potrzebujesz chwili, powiedz krotko:
- "Juz slucham..."
- "Chwileczke..."
- "Prosze powiedziec jeszcze raz..."
- "Juz sprawdzam..."

# Kontekst polaczenia
Numer telefonu dzwoniacego: {caller_phone or 'nieznany'}

# Zasady rozmowy
- Na tym etapie nie uzywasz zadnych narzedzi.
- Nie wymyslasz terminow wizyt.
- Nie potwierdzasz rezerwacji ani zapisow.
- Nie obiecujesz oddzwonienia.
- Nie wspominasz o hotelu, pokojach, noclegach, apartamentach ani rezerwacjach hotelowych.
- Gdy pacjent chce umowic wizyte, powiedz uprzejmie, ze to testowy asystent i popros o krotkie opisanie potrzeby.
- Gdy pacjent pyta o terminy, odpowiedz, ze na razie nie masz dostepu do terminarza.
- Gdy czegos nie rozumiesz, popros o powtorzenie.
- Jedna mysl na raz.
- Nie wygłaszaj dlugich monologow.

# Powitanie
Na poczatku przywitaj rozmowce i zapytaj, w czym mozesz pomoc.

# Zakonczenie rozmowy
Gdy rozmowca konczy rozmowe, odpowiedz krotko i naturalnie, na przyklad:
"Dziekuje za rozmowe. Do widzenia."
"""
        )


def get_caller_phone_from_room_name(room_name: str) -> Optional[str]:
    if not room_name:
        return None
    match = re.search(r"\+?\d{6,}", room_name)
    return match.group(0) if match else None


async def entrypoint(ctx: JobContext):
    logger.info("=" * 50)
    logger.info(f"[Agent] New SIP call! Room: {ctx.room.name}")
    logger.info(f"[Agent] CLINIC_NAME: {CLINIC_NAME}")
    logger.info(f"[Agent] REALTIME_VOICE: {REALTIME_VOICE}")
    logger.info("=" * 50)

    caller_phone = get_caller_phone_from_room_name(ctx.room.name)
    logger.info(f"[Agent] Caller phone (from room name): {caller_phone}")

    agent = MedicalAssistantSIP(caller_phone=caller_phone)

    # Mechanika komunikacyjna zostawiona jak w działającym pliku
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
        instructions=(
            "Przywitaj sie DOKLADNIE tymi slowami: "
            "Dzien dobry, tu medyczny asystent glosowy. W czym moge pomoc?"
        )
    )

    logger.info("[Agent] Greeting generated.")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="lka",
        )
    )
