from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError

from core.api.routes import router
from core.data.queries import MissingMacroWeekError

app = FastAPI(title="Astra API", version="0.1.0")
app.include_router(router)


@app.exception_handler(MissingMacroWeekError)
async def missing_week_handler(request: Request, error: MissingMacroWeekError):
    return JSONResponse(
        status_code=409,
        content={"detail": str(error), "missing_week": error.week.isoformat()},
    )


@app.exception_handler(IntegrityError)
async def integrity_handler(request: Request, error: IntegrityError):
    code = getattr(error.orig, "sqlstate", None)
    if code == "23503":
        message = "Связанная запись отсутствует. Сначала загрузите недельные показатели."
    elif code == "23505":
        message = "Запись с таким ключом уже существует."
    else:
        message = "Данные нарушают ограничения базы данных."
    return JSONResponse(status_code=409, content={"detail": message})


@app.exception_handler(OperationalError)
async def database_unavailable_handler(request: Request, error: OperationalError):
    return JSONResponse(status_code=503, content={"detail": "База данных временно недоступна."})
