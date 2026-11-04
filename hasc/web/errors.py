from fastapi import HTTPException
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Application error adhering to spec error shape:
    {"error": {"code": "<code>", "message": "<Vietnamese text>"}}
    """

    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}

    def to_response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status_code, content=self.to_dict())
