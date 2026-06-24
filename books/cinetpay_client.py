import requests
from django.conf import settings


class CinetpayError(Exception):
    pass


def _request(path, payload):
    """POST to the CinetPay checkout API and return the parsed response.

    Only raises on a transport/JSON failure - the two real endpoints below
    use different "did this call succeed" codes ("201" for initiating a
    payment, "00" for checking one), so each interprets its own response
    rather than this shared helper guessing a single convention.
    """
    url = f"{settings.CINETPAY_BASE_URL}{path}"
    body = {
        "apikey": settings.CINETPAY_API_KEY,
        "site_id": settings.CINETPAY_SITE_ID,
        **payload,
    }

    try:
        response = requests.post(url, json=body, timeout=15)
    except requests.RequestException as exc:
        raise CinetpayError(str(exc)) from exc

    try:
        return response.json()
    except ValueError as exc:
        raise CinetpayError(f"Invalid response ({response.status_code})") from exc


def initiate_payment(transaction_id, amount, currency, description, notify_url, return_url, customer=None, channels="ALL"):
    payload = {
        "transaction_id": transaction_id,
        "amount": amount,
        "currency": currency,
        "description": description[:255],
        "notify_url": notify_url,
        "return_url": return_url,
        "channels": channels,
    }
    if customer:
        payload.update({
            "customer_name": customer.get("name", ""),
            "customer_surname": customer.get("surname", ""),
            "customer_email": customer.get("email", ""),
            "customer_phone_number": customer.get("phone", ""),
        })

    data = _request("/payment", payload)
    if str(data.get("code")) != "201":
        raise CinetpayError(data.get("message") or data.get("description") or "CinetPay payment initialization failed")
    return data


def check_payment_status(transaction_id):
    """Re-query CinetPay server-side for the real status of a transaction.

    Per CinetPay's own guidance, the notify_url webhook body itself isn't
    trusted as proof of payment - it only tells you which transaction_id to
    look up here. A "00" code means the check call itself succeeded; the
    actual payment outcome is in data["data"]["status"] (e.g. "ACCEPTED").
    """
    data = _request("/payment/check", {"transaction_id": transaction_id})
    if str(data.get("code")) != "00":
        raise CinetpayError(data.get("message") or data.get("description") or "CinetPay payment check failed")
    return data
