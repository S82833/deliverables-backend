from fastapi import FastAPI, Query, HTTPException, Header
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
import os, random
from dotenv import load_dotenv
import traceback
from collections import defaultdict
from typing import Optional
from datetime import datetime
from pyairtable import Api
import requests
import time
from typing import Dict, Any, Tuple

CACHE: Dict[str, Tuple[float, Any]] = {}
CACHE_TTL = 60 * 60 * 1  # seconds

def cache_get(key: str):
    item = CACHE.get(key)
    if not item:
        return None

    expires_at, value = item
    if time.time() > expires_at:
        del CACHE[key]
        return None

    return value


def cache_set(key: str, value: Any, ttl: int = CACHE_TTL):
    CACHE[key] = (time.time() + ttl, value)


def resolve_redirect(url: str) -> str:
    try:
        r = requests.get(url, allow_redirects=True, timeout=5)
        return r.url
    except Exception:
        return url
    
load_dotenv()

AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")
BASE_ID = os.getenv("BASE_ID")
TABLE_ID = os.getenv("TABLE_ID")
VIEW_ID = os.getenv("VIEW_ID")
PHONE_FIELD = os.getenv("PHONE_FIELD")

print("AIRTABLE_PAT loaded:", bool(AIRTABLE_PAT))
print("BASE_ID loaded:", bool(BASE_ID))
print("TABLE_ID loaded:", bool(TABLE_ID))
print("VIEW_ID loaded:", bool(VIEW_ID))
print("PHONE_FIELD loaded:", bool(PHONE_FIELD))
AIRTABLE_WEBHOOK_SECRET = os.getenv("AIRTABLE_WEBHOOK_SECRET")

print("AIRTABLE_WEBHOOK_SECRET loaded:", bool(AIRTABLE_WEBHOOK_SECRET))

api = Api(AIRTABLE_PAT)
table = api.table(BASE_ID, TABLE_ID)

def update_cache_with_record(record: dict):
    fields = record.get("fields", {})
    phone = fields.get("Celular")

    if not phone:
        return

    key = f"deliverables:{phone}"

    cached = cache_get(key)
    if not cached:
        # Si aún no hay cache para ese phone, inicializamos
        cached = {"records": []}

    records = cached.get("records", [])

    updated = False
    for i, r in enumerate(records):
        if r.get("id") == record.get("id"):
            records[i] = record
            updated = True
            break

    if not updated:
        records.append(record)

    cached["records"] = records
    cache_set(key, cached)



app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex = r"^https://deliverables-frontend(-.*)?\.vercel\.app$|^https://.*\.use2\.devtunnels\.ms$|^http://localhost:5173$|^http://localhost:3000$|^http://127\.0\.0\.1:5500$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return RedirectResponse(url="/docs")

@app.get("/deliverables")
async def get_deliverables(
    phone: Optional[str] = Query(None, description="Filter by phone number"),
):
    try:
        phone_key = phone or "ALL"
        cache_key = f"deliverables:{phone_key}"

        cached = cache_get(cache_key)
        if cached:
            return cached

        phone_filter = f"TIKTOK USA {phone}" if phone else None

        records = table.all(
            view=VIEW_ID,
            formula=f"{{{PHONE_FIELD}}} = '{phone_filter}'" if phone_filter else None,
            fields=[
                "EntregableID",
                "Dia de Entregable",
                "Name (from 1 Cuenta)",
                "Crewstr",
                "Celular",
                "Sound Link",
                "Text to use on post",
                "Link Cover Image",
                "Short Hooks Images",
                "Link To Short hook Image",
                "Book - Author - Tropes",
                "Hashtags for post",
                "Numero de Post",
                "LINK PARA REPORTAR EL POST"
            ],
        )

        # for r in records:
        #     f = r.get("fields", {})
        #     link = f.get("Link Cover Image")
        #     if isinstance(link, list) and link:
        #         f["Link Cover Image Final"] = resolve_redirect(link[0])

        response = {"records": records}

        cache_set(cache_key, response)

        return response

    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})
    
@app.post("/airtable/event")
async def airtable_event(payload: dict, x_airtable_secret: str = Header(None)):
    if x_airtable_secret != AIRTABLE_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")

    record_id = payload.get("record_id")
    phone = payload.get("phone")
    fields = payload.get("fields")

    if not record_id or not phone or not fields:
        raise HTTPException(status_code=400, detail="Missing record_id, phone or fields")

    try:
        record = {
            "id": record_id,
            "fields": fields
        }
        update_cache_with_record(record)
        return {"status": "ok"}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/airtable/warmup")
async def airtable_warmup(payload: dict, x_airtable_secret: str = Header(None)):
    if x_airtable_secret != AIRTABLE_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")

    records = payload.get("records")
    if not isinstance(records, list):
        raise HTTPException(status_code=400, detail="Missing or invalid records list")

    try:
        # 1. Limpiar cache
        CACHE.clear()

        # 2. Agrupar por Celular
        grouped = defaultdict(list)
        for r in records:
            fields = r.get("fields", {})
            phone = fields.get("Celular")
            if phone:
                grouped[phone].append(r)

        # 3. Guardar cada grupo en cache
        for phone, recs in grouped.items():
            key = f"deliverables:{phone}"
            cache_set(key, {"records": recs})

        return {
            "status": "ok",
            "phones_loaded": len(grouped),
            "records_loaded": len(records),
        }

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

from fastapi import Request

@app.get("/debug-origin")
async def debug_origin(request: Request):
    return {"origin": request.headers.get("origin")}
