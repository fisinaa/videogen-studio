from app.main import app
from app.routes.openmontage import router as openmontage_router


app.include_router(openmontage_router)
