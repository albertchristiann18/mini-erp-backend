class ShopeeAPIError(Exception):
    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        super().__init__(f"[{error_code}] {message}")


class ShopeeAuthError(ShopeeAPIError):
    pass
