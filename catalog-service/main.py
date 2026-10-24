from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Catalog Service")


class Space(BaseModel):
    id: int
    name: str
    capacity: int
    price_per_hour: float


SPACES = [
    Space(id=1, name="Sala Creativa", capacity=6, price_per_hour=25.0),
    Space(id=2, name="Escritorio Compartido", capacity=1, price_per_hour=8.0),
    Space(id=3, name="Sala de Juntas", capacity=12, price_per_hour=40.0),
]


@app.get("/health")
def health():
    return {"status": "ok", "service": "catalog-service"}


@app.get("/spaces")
def list_spaces():
    return SPACES


@app.get("/spaces/{space_id}")
def get_space(space_id: int):
    for space in SPACES:
        if space.id == space_id:
            return space
    raise HTTPException(status_code=404, detail="Space not found")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)