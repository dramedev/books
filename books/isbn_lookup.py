import requests


OPEN_LIBRARY_URL = "https://openlibrary.org/api/books"


class IsbnLookupError(Exception):
    pass


def lookup_isbn(isbn):
    isbn = (isbn or "").strip()
    if not isbn:
        raise IsbnLookupError("ISBN is required.")

    bibkey = f"ISBN:{isbn}"

    try:
        response = requests.get(
            OPEN_LIBRARY_URL,
            params={"bibkeys": bibkey, "jscmd": "data", "format": "json"},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise IsbnLookupError(str(exc)) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise IsbnLookupError("Invalid response from book lookup service.") from exc

    record = data.get(bibkey)
    if not record:
        raise IsbnLookupError("No book found for this ISBN.")

    cover = record.get("cover") or {}

    return {
        "title": record.get("title", ""),
        "subtitle": record.get("subtitle", ""),
        "publishers": [p.get("name") for p in record.get("publishers", []) if p.get("name")],
        "publish_date": record.get("publish_date", ""),
        "authors": [a.get("name") for a in record.get("authors", []) if a.get("name")],
        "cover_url": cover.get("large") or cover.get("medium") or "",
    }
