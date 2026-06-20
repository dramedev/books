import base64
import hashlib
import hmac
import json
import random
import time

import requests
from django.conf import settings


class IyzicoError(Exception):
    pass


def _random_key():
    return f"{int(time.time() * 1000)}{random.randint(100000, 999999)}"


def _auth_header(uri_path, body_str):
    random_key = _random_key()
    to_sign = f"{random_key}{uri_path}{body_str}"
    signature = hmac.new(
        settings.IYZICO_SECRET_KEY.encode("utf-8"), to_sign.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    auth_params = f"apiKey:{settings.IYZICO_API_KEY}&randomKey:{random_key}&signature:{signature}"
    encoded = base64.b64encode(auth_params.encode("utf-8")).decode("ascii")
    return random_key, f"IYZWSv2 {encoded}"


def _request(method, path, payload=None):
    body_str = json.dumps(payload, separators=(",", ":")) if payload is not None else ""
    random_key, auth_header = _auth_header(path, body_str)
    headers = {
        "Authorization": auth_header,
        "x-iyzi-rnd": random_key,
        "Content-Type": "application/json",
    }
    url = f"{settings.IYZICO_BASE_URL}{path}"

    try:
        response = requests.request(
            method, url, headers=headers,
            data=body_str if payload is not None else None,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise IyzicoError(str(exc)) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise IyzicoError(f"Invalid response ({response.status_code})") from exc

    if data.get("status") != "success":
        raise IyzicoError(data.get("errorMessage") or f"iyzico request failed ({response.status_code})")

    return data


def initialize_subscription_checkout_form(pricing_plan_reference_code, callback_url, customer):
    payload = {
        "callbackUrl": callback_url,
        "pricingPlanReferenceCode": pricing_plan_reference_code,
        "subscriptionInitialStatus": "ACTIVE",
        "customer": customer,
    }
    return _request("POST", "/v2/subscription/checkoutform/initialize", payload)


def retrieve_checkout_form(token):
    return _request("GET", f"/v2/subscription/checkoutform/{token}")


def initialize_card_update_checkout_form(customer_reference_code, callback_url):
    payload = {
        "callbackUrl": callback_url,
        "customerReferenceCode": customer_reference_code,
    }
    return _request("POST", "/v2/subscription/card-update/checkoutform/initialize", payload)


def verify_webhook_signature(
    merchant_id, event_type, subscription_reference_code,
    order_reference_code, customer_reference_code, signature_header,
):
    to_sign = (
        f"{merchant_id}{settings.IYZICO_SECRET_KEY}{event_type}"
        f"{subscription_reference_code}{order_reference_code}{customer_reference_code}"
    )
    expected = hmac.new(
        settings.IYZICO_SECRET_KEY.encode("utf-8"), to_sign.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")
