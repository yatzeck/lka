import os
import logging
from datetime import date
from typing import Any, Optional

import httpx

logger = logging.getLogger("wp-front-ajax-client")


class FrontAjaxClient:
    """Thin async client for WordPress front_ajax.php used by the demo agent.

    Notes:
    - Uses JSON actions when front_ajax.php registers them in the top action map.
    - Uses multipart/form POST for legacy actions like WolneTerminy / TerminZapisz / AppointmentCancel.
    - A few JSON actions (PatientVisits / AppointmentGet / AppointmentReschedule) are inferred from
      front_ajax.php routing but their exact backend contract isn't visible in uploaded files.
      The client therefore tries a conservative payload and returns backend output verbatim.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("FRONT_AJAX_URL") or "").rstrip("/")
        self.api_key = api_key or os.environ.get("FRONT_AJAX_API_KEY") or os.environ.get("SMS_API_KEY") or ""
        self.timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _headers(self, json_mode: bool = True) -> dict[str, str]:
        headers = {"X-API-KEY": self.api_key}
        if json_mode:
            headers["Content-Type"] = "application/json"
        return headers

    async def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "FRONT_AJAX_URL or FRONT_AJAX_API_KEY missing"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.post(self.base_url, json=payload, headers=self._headers(json_mode=True))
                text = response.text
                response.raise_for_status()
                try:
                    data = response.json()
                except Exception:
                    data = {"raw": text}
                return {"ok": True, "data": data, "status_code": response.status_code}
        except httpx.HTTPStatusError as e:
            return {
                "ok": False,
                "error": f"HTTP {e.response.status_code}",
                "details": e.response.text[:2000],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def _post_form(self, form: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "FRONT_AJAX_URL or FRONT_AJAX_API_KEY missing"}
        safe_form = {k: "" if v is None else str(v) for k, v in form.items()}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.post(self.base_url, data=safe_form, headers={"X-API-KEY": self.api_key})
                text = response.text
                response.raise_for_status()
                try:
                    data = response.json()
                except Exception:
                    data = {"raw": text}
                return {"ok": True, "data": data, "status_code": response.status_code}
        except httpx.HTTPStatusError as e:
            return {
                "ok": False,
                "error": f"HTTP {e.response.status_code}",
                "details": e.response.text[:2000],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def normalize_phone(phone: Optional[str]) -> str:
        if not phone:
            return ""
        digits = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
        digits = digits.replace(" ", "").replace("-", "")
        if digits.startswith("0048"):
            digits = "+48" + digits[4:]
        elif digits.startswith("48") and len(digits) == 11:
            digits = "+" + digits
        elif digits.startswith("+"):
            pass
        elif len(digits) == 9:
            digits = "+48" + digits
        return digits

    @staticmethod
    def _today_str() -> str:
        return date.today().isoformat()

    async def doctor_get(self, doctor_id: str) -> dict[str, Any]:
        return await self._post_json({
            "action": "Doctors",
            "doctor_id": doctor_id,
        })

    async def patient_get(
        self,
        *,
        phone: Optional[str] = None,
        first_name: str = "",
        last_name: str = "",
        email: str = "",
        pesel: str = "",
    ) -> dict[str, Any]:
        payload = {
            "action": "PatientGet",
            "gdzie": "bot",
        }
    
        normalized_phone = self.normalize_phone(phone)
        if normalized_phone:
            payload["telefon"] = normalized_phone
    
        if first_name:
            payload["imie"] = first_name
        if last_name:
            payload["nazwisko"] = last_name
        if email:
            payload["email"] = email
        if pesel:
            payload["pesel"] = pesel
    
        return await self._post_json(payload)

    async def patient_add(
        self,
        *,
        first_name: str,
        last_name: str,
        phone: Optional[str],
        email: str = "",
    ) -> dict[str, Any]:
        normalized_phone = self.normalize_phone(phone)
        payload = {
            "action": "PatientAdd",
            "imie": first_name,
            "nazwisko": last_name,
            "telefon": normalized_phone,
            "kontakt": normalized_phone,
            "email": email,
            "gdzie": "bot",
        }
        return await self._post_json(payload)

    async def patient_resolve_or_create(
        self,
        *,
        caller_phone: Optional[str],
        first_name: str = "",
        last_name: str = "",
        email: str = "",
    ) -> dict[str, Any]:
        # 1. try exact phone match first - this is supported by psga_PacjentGet in uploaded plugin code
        if caller_phone:
            existing = await self.patient_get(phone=caller_phone)
            if existing.get("ok") and existing.get("data"):
                return existing

        # 2. try by name+phone when both exist
        if caller_phone and first_name and last_name:
            existing = await self.patient_get(
                phone=caller_phone,
                first_name=first_name,
                last_name=last_name,
                email=email,
            )
            if existing.get("ok") and existing.get("data"):
                return existing

        # 3. create only if enough data available
        if first_name and last_name and caller_phone:
            created = await self.patient_add(
                first_name=first_name,
                last_name=last_name,
                phone=caller_phone,
                email=email,
            )
            return created

        return {
            "ok": False,
            "error": "patient_not_resolved",
            "details": "Need at least caller phone or full patient data",
        }

    async def free_terms(self, *, doctor_id: str, date_from: str) -> dict[str, Any]:
        return await self._post_form({
            "action": "WolneTerminy",
            "lekarz_id": doctor_id,
            "data_od": date_from,
        })

    async def appointment_book(
        self,
        *,
        doctor_id: str,
        start_dt: str,
        end_dt: str,
        patient_id: str,
        rodzaj: str = "wizyta",
    ) -> dict[str, Any]:
        return await self._post_form({
            "action": "TerminZapisz",
            "lekarz_id": doctor_id,
            "start": start_dt,
            "end": end_dt,
            "pcj_id": patient_id,
            "rodzaj": rodzaj,
        })

    async def appointment_cancel(self, *, pw_id: str) -> dict[str, Any]:
        return await self._post_form({
            "action": "AppointmentCancel",
            "pw_id": pw_id,
        })

    async def patient_visits(
        self,
        *,
        patient_id: Optional[str] = None,
        phone: Optional[str] = None,
        doctor_id: Optional[str] = None,
    ) -> dict[str, Any]:
        # Contract inferred from front_ajax action map. Backend may ignore unknown fields.
        payload: dict[str, Any] = {"action": "PatientVisits"}
        if patient_id:
            payload["pcj_id"] = patient_id
        if phone:
            normalized_phone = self.normalize_phone(phone)
            payload["telefon"] = normalized_phone
            payload["kontakt"] = normalized_phone
        if doctor_id:
            payload["doctor_id"] = doctor_id
            payload["lekarz_id"] = doctor_id
        payload["future_only"] = 1
        payload["date_from"] = self._today_str()
        return await self._post_json(payload)

    async def appointment_get(
        self,
        *,
        patient_id: Optional[str] = None,
        phone: Optional[str] = None,
        doctor_id: Optional[str] = None,
    ) -> dict[str, Any]:
        # Contract inferred from front_ajax action map.
        payload: dict[str, Any] = {"action": "AppointmentGet"}
        if patient_id:
            payload["pcj_id"] = patient_id
        if phone:
            normalized_phone = self.normalize_phone(phone)
            payload["telefon"] = normalized_phone
            payload["kontakt"] = normalized_phone
        if doctor_id:
            payload["doctor_id"] = doctor_id
            payload["lekarz_id"] = doctor_id
        payload["future_only"] = 1
        payload["date_from"] = self._today_str()
        return await self._post_json(payload)

    async def appointment_lookup(
        self,
        *,
        patient_id: Optional[str] = None,
        phone: Optional[str] = None,
        doctor_id: Optional[str] = None,
    ) -> dict[str, Any]:
        first = await self.patient_visits(patient_id=patient_id, phone=phone, doctor_id=doctor_id)
        if first.get("ok") and first.get("data"):
            return first
        second = await self.appointment_get(patient_id=patient_id, phone=phone, doctor_id=doctor_id)
        return second

    async def appointment_reschedule(
        self,
        *,
        pw_id: str,
        new_start: str,
        new_end: str,
        doctor_id: Optional[str] = None,
    ) -> dict[str, Any]:
        # Contract inferred from front_ajax action map.
        payload: dict[str, Any] = {
            "action": "AppointmentReschedule",
            "pw_id": pw_id,
            "new_start": new_start,
            "new_end": new_end,
        }
        if doctor_id:
            payload["doctor_id"] = doctor_id
            payload["lekarz_id"] = doctor_id
        return await self._post_json(payload)

    @staticmethod
    def compact_patient(data: Any) -> dict[str, Any]:
        if isinstance(data, list):
            row = data[0] if data else {}
        elif isinstance(data, dict):
            row = data
        else:
            row = {}
        return {
            "pcj_id": str(row.get("pcj_id", "")),
            "first_name": row.get("pcj_imie") or row.get("imie") or "",
            "last_name": row.get("pcj_nazwisko") or row.get("nazwisko") or "",
            "phone": row.get("telefon") or row.get("pcj_kontakt") or row.get("kontakt") or "",
            "email": row.get("pcj_email") or row.get("email") or "",
        }

    @staticmethod
    def compact_slots(data: Any, *, date_to: str = "", time_of_day: str = "") -> list[dict[str, Any]]:
        rows = data if isinstance(data, list) else []
        out: list[dict[str, Any]] = []
        for row in rows:
            slot_date = str(row.get("data", ""))
            godz_od = str(row.get("godz_od", ""))
            godz_do = str(row.get("godz_do", ""))
            if date_to and slot_date and slot_date > date_to:
                continue
            if time_of_day:
                hh = int(godz_od[:2]) if godz_od and godz_od[:2].isdigit() else -1
                if time_of_day == "rano" and not (0 <= hh < 12):
                    continue
                if time_of_day == "popoludnie" and not (12 <= hh < 17):
                    continue
                if time_of_day == "wieczor" and not (17 <= hh <= 21):
                    continue
            if slot_date and godz_od and godz_do:
                out.append({
                    "slot_id": f"{slot_date}T{godz_od}",
                    "date": slot_date,
                    "start_time": godz_od,
                    "end_time": godz_do,
                    "start": f"{slot_date} {godz_od}:00",
                    "end": f"{slot_date} {godz_do}:00",
                    "day_name": row.get("dzien", ""),
                    "duration": row.get("czas", ""),
                })
        return out

    @staticmethod
    def choose_slot(
        slots: list[dict[str, Any]],
        *,
        slot_id: str = "",
        appointment_date: str = "",
        appointment_time: str = "",
    ) -> Optional[dict[str, Any]]:
        if slot_id:
            for slot in slots:
                if slot.get("slot_id") == slot_id:
                    return slot
        if appointment_date and appointment_time:
            for slot in slots:
                if slot.get("date") == appointment_date and slot.get("start_time") == appointment_time:
                    return slot
        if appointment_date:
            for slot in slots:
                if slot.get("date") == appointment_date:
                    return slot
        return slots[0] if slots else None

    @staticmethod
    def choose_visit(
        visits_data: Any,
        *,
        pw_id: str = "",
        appointment_date: str = "",
    ) -> Optional[dict[str, Any]]:
        rows = visits_data if isinstance(visits_data, list) else []
        if isinstance(visits_data, dict):
            # some APIs wrap list under data/items/visits
            for key in ("items", "visits", "appointments", "data"):
                if isinstance(visits_data.get(key), list):
                    rows = visits_data[key]
                    break
        if pw_id:
            for row in rows:
                if str(row.get("pw_id", "")) == str(pw_id):
                    return row
        if appointment_date:
            for row in rows:
                start = str(row.get("pw_start") or row.get("start") or row.get("date") or "")
                if start.startswith(appointment_date):
                    return row
        return rows[0] if rows else None
