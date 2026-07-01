from fastapi import APIRouter

from app.schemas import CatalogueRequest

router = APIRouter()

@router.post("/get_catalogue")
async def get_catalogue(catalogue_request: CatalogueRequest):
    
    return {"message": "Catalogue endpoint is working!"}