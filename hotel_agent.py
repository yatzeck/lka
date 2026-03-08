"""
RIRIKA Hotel Voice Agent - SIP Version v2
For Railway deployment - handles inbound phone calls via LiveKit SIP

Based on official LiveKit Agents starter (January 2026)
Updated with OpenAI Realtime best practices for voice agents
"""

import os
import logging
import httpx
from typing import Any
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

load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hotel-agent-sip")

# TYMCZASOWY HARDCODE - Railway nie aktualizuje env vars
API_BASE_URL = "https://dev.test1.gabinet.plus"
SMS_API_KEY = os.environ.get("SMS_API_KEY", "")


async def call_api(function_name: str, arguments: dict) -> dict:
    """Call backend API for general functions."""
    if not API_BASE_URL:
        logger.error("[API] API_BASE_URL not set!")
        return {"error": "API_BASE_URL not configured"}
    
    url = f"{API_BASE_URL}/api/realtime/functions"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            logger.info(f"[API] POST {url}")
            logger.info(f"[API] Function: {function_name}, Args: {arguments}")
            
            response = await client.post(
                url,
                json={
                    "function_name": function_name,
                    "arguments": arguments,
                },
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            result = response.json()
            logger.info(f"[API] Success, keys: {list(result.keys()) if isinstance(result, dict) else 'not dict'}")
            return result
        except httpx.HTTPStatusError as e:
            logger.error(f"[API] HTTP {e.response.status_code}: {e.response.text[:200]}")
            return {"error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            logger.error(f"[API] Error: {e}")
            return {"error": str(e)}


async def call_voice_api(endpoint: str, payload: dict) -> dict:
    """Call voice-specific API endpoints with SMS_API_KEY auth."""
    if not API_BASE_URL:
        logger.error("[API] API_BASE_URL not set!")
        return {"error": "API_BASE_URL not configured"}
    
    if not SMS_API_KEY:
        logger.error("[API] SMS_API_KEY not set!")
        return {"error": "SMS_API_KEY not configured"}
    
    url = f"{API_BASE_URL}{endpoint}"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            logger.info(f"[Voice API] POST {url}")
            logger.info(f"[Voice API] Payload: {payload}")
            
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": SMS_API_KEY,
                },
            )
            response.raise_for_status()
            result = response.json()
            logger.info(f"[Voice API] Success: {result}")
            return result
        except httpx.HTTPStatusError as e:
            logger.error(f"[Voice API] HTTP {e.response.status_code}: {e.response.text[:500]}")
            return {"error": f"HTTP {e.response.status_code}", "details": e.response.text[:500]}
        except Exception as e:
            logger.error(f"[Voice API] Error: {e}")
            return {"error": str(e)}


class HotelAssistantSIP(Agent):
    """Hotel voice assistant for SIP calls - no email, SMS only."""
    
    def __init__(self, caller_phone: str = None) -> None:
        self.caller_phone = caller_phone
        logger.info(f"[Agent] Initialized with caller_phone: {caller_phone}")
        
        # System prompt zgodny z OpenAI Realtime best practices
        # Format numeru do odczytu: +48 ### ### ###
        caller_phone_spoken = caller_phone
        if caller_phone and caller_phone.startswith("+48"):
            digits = caller_phone[3:].replace(" ", "").replace("-", "")
            if len(digits) == 9:
                caller_phone_spoken = f"plus czterdziesci osiem, {digits[0]}, {digits[1]}, {digits[2]}, {digits[3]}, {digits[4]}, {digits[5]}, {digits[6]}, {digits[7]}, {digits[8]}"
        
        super().__init__(
            instructions=f"""
# Rola i cel
Jestes Ririka, recepcjonistka w Goralskim Wzgorzu w Zakopanem. Pomagasz gosciom rezerwowac pokoje i apartamenty przez telefon.

WAZNE: NIGDY nie uzywaj slow "hotel" ani "pensjonat". Mow zawsze "Goralskie Wzgorze" lub "obiekt" lub "u nas".

# Osobowosc i ton
## Osobowosc
Spokojna, ciepla, empatyczna i profesjonalna. Sluchasz uwaznie i odpowiadasz rzeczowo.

## Ton
Cieplo-serdeczny, opanowany, pewny siebie. Nigdy nie brzmisz sztucznie podekscytowana ani nadgorliwie.

## Dlugosc wypowiedzi
Maksymalnie 2-3 zdania na raz. Mow zwiezle.

## Tempo
Mow spokojnie i wyraznie, bez pospieszania.

## Jezyk
Tylko polski. Nie uzywaj emoji, gwiazdek ani formatowania.

# NAJWAZNIEJSZA ZASADA - ZERO CISZY
NIGDY nie zostawiaj klienta w ciszy! Zawsze mow co robisz:
- Przed wyszukiwaniem: "Przygotowuje oferte, chwileczke..."
- Przed tworzeniem rezerwacji: "Robie rezerwacje i generuje link do platnosci, prosze chwile poczekac..."
- Przy dluzszym oczekiwaniu: "Juz prawie gotowe..."
- Przy dlugo trwajacej operacji dodaj: "Dziekuje za cierpliwosc..."

# Kontekst polaczenia
Numer telefonu dzwoniacego: {caller_phone or 'nieznany'}
Numer do odczytu glosowego: {caller_phone_spoken or 'nieznany'}

# Flow rezerwacji telefonicznej

## 1. Powitanie
DOKLADNE POWITANIE (nie zmieniaj!): "Witamy w Goralskim Wzgorzu. Pokoje i apartamenty w Zakopanem. W czym moge pomoc?"

## 2. Zbieranie informacji o terminie
Cel: Ustalic termin pobytu.
Pytanie: "Na kiedy planuje Pan/Pani przyjazd i ile nocy?"

## 3. Zbieranie informacji o gosciach - BARDZO WAZNE
ZAWSZE dopytaj dokladnie o sklad gosci:
- "Ile bedzie osob doroslych? Wliczam w to tez dzieci od jedenastego roku zycia."
- "Czy beda dzieci do dziesieciu lat? Jesli tak, to ile?"

Zapisz:
- Doroslych = osoby 11 lat i wiecej
- Dzieci = osoby do 10 lat wlacznie

## 4. Wyszukiwanie pokoi
Cel: Znalezc dostepne opcje uzywajac narzedzia search_rooms.
PRZED wywolaniem POWIEDZ: "Przygotowuje oferte, chwileczke..."

WAZNE - Po otrzymaniu wynikow z search_rooms:
- Przeczytaj DOKLADNIE dane z API (title, price_per_night, total_price, deposit_amount)
- Przedstaw klientowi TYLKO pokoje z wynikow API
- Podaj DOKLADNE ceny z API - NIE ZAOKRAGLAJ, NIE ZGADUJ
- Przyklad: "Mamy dostepny [title z API] w cenie [price_per_night] zlotych za noc, czyli [total_price] za caly pobyt. Zadatek to [deposit_amount] zlotych."

## 5. Rozpoznawanie intencji rezerwacji
BADZ WYCZULONY na wypowiedzi klienta typu:
- "biere", "rezerwuje", "chce zarezerwowac", "decyduje sie", "to bede chcial/a", "to mi odpowiada"

Gdy rozpoznasz taka intencje, NATYCHMIAST POTWIERDZ szczegoly:
"Potwierdzam - rezerwuje dla Pana/Pani [nazwa pokoju] na termin od [data przyjazdu] do [data wyjazdu], dla [liczba doroslych] doroslych i [liczba dzieci] dzieci. Cena calkowita to [kwota] zlotych. Zadatek wynosi [kwota zadatku] zlotych i jest na niego godzina. Czy wszystko sie zgadza?"

## 6. Dane goscia
Cel: Zebrac imie i nazwisko (BEZ emaila!).
Pytanie: "Prosze o imie i nazwisko na rezerwacje."

## 7. Potwierdzenie numeru telefonu
Cel: Upewnic sie na jaki numer wyslac SMS.
Odczytaj numer CYFRA PO CYFRZE: "Mam Pana/Pani numer {caller_phone_spoken}. Czy moge na ten numer wyslac link do platnosci?"
- Jesli TAK - uzyj caller_phone
- Jesli NIE - zapytaj: "Na jaki numer wyslac SMS?" i zapisz nowy numer

## 8. Finalizacja
Cel: Utworzyc rezerwacje i poinformowac o SMS.
PRZED wywolaniem POWIEDZ: "Robie rezerwacje i generuje link do platnosci, prosze chwile poczekac..."
Po sukcesie: "Gotowe! Wyslalem SMS z linkiem do platnosci zadatku. Na oplacenie zadatku ma Pan/Pani godzine. Czy moge w czyms jeszcze pomoc?"

## 9. Zakonczenie rozmowy
Po zakonczeniu rezerwacji lub gdy klient nie potrzebuje juz pomocy:
"Dziekuje bardzo za rozmowe i zycze milego dnia. Do widzenia!"

# KRYTYCZNE - UZYWAJ TYLKO DANYCH Z NARZEDZI!
ABSOLUTNIE ZAKAZANE jest wymyslanie lub zgadywanie:
- Nazw pokoi - TYLKO te zwrocone przez search_rooms
- Cen - TYLKO ceny z wynikow search_rooms (pole total_price, price_per_night)
- Dostepnosci - TYLKO pokoje ktore sa w wynikach search_rooms
- Kwot zadatku - TYLKO deposit_amount z wynikow
- Pojemnosci pokoi - TYLKO max_guests z wynikow

Gdy klient pyta o pokoje, MUSISZ:
1. Wywolac search_rooms z podanymi datami i liczba gosci
2. POCZEKAC na wyniki
3. Opisac TYLKO pokoje zwrocone przez API
4. Uzywac DOKLADNYCH cen z API (total_price, deposit_amount)

Jesli search_rooms zwroci pusta liste - powiedz ze nie ma dostepnych pokoi w tym terminie.
Jesli search_rooms zwroci blad - przepros i popros o powtorzenie dat.

# Wazne zasady
- NIGDY nie mow "hotel" ani "pensjonat" - tylko "Goralskie Wzgorze" lub "obiekt"
- NIE pytaj o email - cala komunikacja przez SMS
- ZAWSZE podsumuj rezerwacje przed jej utworzeniem
- ZAWSZE potwierdz numer telefonu przed wysylka SMS (czytaj cyfra po cyfrze)
- ZAWSZE informuj ze na platnosc zadatku jest godzina
- Jesli nie wiesz odpowiedzi - powiedz ze przekazesz do recepcji
- Po zakonczeniu rezerwacji PODZIEKUJ i POZEGNAJ SIE
""",
        )

    @function_tool
    async def search_rooms(
        self,
        context: RunContext,
        check_in: str,
        check_out: str,
        guests: int,
    ) -> dict[str, Any]:
        """Wyszukuje dostepne pokoje w hotelu na podane daty.
        
        Args:
            check_in: Data zameldowania w formacie YYYY-MM-DD (np. 2026-02-15)
            check_out: Data wymeldowania w formacie YYYY-MM-DD (np. 2026-02-18)
            guests: Laczna liczba gosci
        """
        logger.info(f"[Tool] search_rooms: {check_in} -> {check_out}, {guests} guests")
        
        return await call_api("get_complete_hotel_data_jsonl", {
            "check_in": check_in,
            "check_out": check_out,
            "guests": guests,
        })

    @function_tool
    async def get_room_details(
        self,
        context: RunContext,
        room_id: str,
    ) -> dict[str, Any]:
        """Pobiera szczegolowe informacje o konkretnym pokoju.
        
        Args:
            room_id: ID pokoju do sprawdzenia
        """
        logger.info(f"[Tool] get_room_details: {room_id}")
        
        return await call_api("get_listing_details", {
            "listing_id": room_id,
        })

    @function_tool
    async def check_room_availability(
        self,
        context: RunContext,
        room_id: str,
        check_in: str,
        check_out: str,
    ) -> dict[str, Any]:
        """Sprawdza czy konkretny pokoj jest dostepny w podanym terminie.
        
        Args:
            room_id: ID pokoju do sprawdzenia
            check_in: Data zameldowania w formacie YYYY-MM-DD
            check_out: Data wymeldowania w formacie YYYY-MM-DD
        """
        logger.info(f"[Tool] check_room_availability: {room_id}, {check_in} -> {check_out}")
        
        return await call_api("check_listing_availability", {
            "listing_id": room_id,
            "check_out": check_out,
            "check_in": check_in,
        })

    @function_tool
    async def create_voice_reservation(
        self,
        context: RunContext,
        room_id: str,
        check_in: str,
        check_out: str,
        guest_name: str,
        adults: int,
        children: int = 0,
        guest_phone: str = None,
    ) -> dict[str, Any]:
        """Tworzy rezerwacje pokoju przez telefon (bez emaila). System automatycznie wysle SMS z linkiem do platnosci.
        
        Args:
            room_id: ID pokoju do zarezerwowania
            check_in: Data zameldowania w formacie YYYY-MM-DD
            check_out: Data wymeldowania w formacie YYYY-MM-DD
            guest_name: Imie i nazwisko goscia
            adults: Liczba doroslych (osoby 11 lat i starsze)
            children: Liczba dzieci (do 10 lat wlacznie)
            guest_phone: Numer telefonu do wyslania SMS (jesli inny niz caller_phone)
        """
        phone_to_use = guest_phone or self.caller_phone
        
        if not phone_to_use:
            logger.error("[Tool] No phone number available!")
            return {"error": "Brak numeru telefonu. Prosze podac numer na ktory wyslac SMS."}
        
        logger.info(f"[Tool] create_voice_reservation: {room_id}, {guest_name}, phone: {phone_to_use}")
        
        return await call_voice_api("/api/voice/create-reservation", {
            "room_id": room_id,
            "check_in": check_in,
            "check_out": check_out,
            "adults": adults,
            "children": children,
            "guest_name": guest_name,
            "caller_phone": self.caller_phone,
            "guest_phone": phone_to_use,
            "source": "voice",
        })


def get_caller_phone_from_room_name(room_name: str) -> str | None:
    """Extract caller phone from room name using regex fallback."""
    import re
    if not room_name:
        return None
    match = re.search(r"\+?\d{6,}", room_name)
    return match.group(0) if match else None


async def entrypoint(ctx: JobContext):
    """Main entry point - called when a new SIP call comes in."""
    logger.info("=" * 50)
    logger.info(f"[Agent] New SIP call! Room: {ctx.room.name}")
    logger.info(f"[Agent] API_BASE_URL: {API_BASE_URL}")
    logger.info(f"[Agent] SMS_API_KEY set: {bool(SMS_API_KEY)}")
    logger.info("=" * 50)
    
    caller_phone = get_caller_phone_from_room_name(ctx.room.name)
    logger.info(f"[Agent] Caller phone (from room name): {caller_phone}")
    
    agent = HotelAssistantSIP(caller_phone=caller_phone)
    
    # Zmiana na glos kobiecy - "shimmer" jest cieplym, spokojnym glosem zenskim
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="shimmer"),
        vad=silero.VAD.load(),
    )
    
    await session.start(
        agent=agent,
        room=ctx.room,
    )
    
    await ctx.connect()
    
    logger.info("[Agent] Session started, generating greeting...")
    
    await session.generate_reply(
        instructions="Przywitaj sie DOKLADNIE tymi slowami: Witamy. Jestem Anna W czym moge pomoc?"
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="lka",
        )
    )
