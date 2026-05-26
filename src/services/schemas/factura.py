"""
Schema Pydantic de FACTURA_COMERCIAL.

Mismas claves que el JSON declarado en prompts/factura.py.
Aliases agregados según errores observados en outputs reales de Qwen3:14b:
  total → total_amount, incoterms → incoterm, tax → tax_breakdown,
  freight → freight_cost, insurance → insurance_cost,
  shipment.vessel → shipment.vessel_or_flight,
  shipment.container_number (str) → shipment.container_numbers (list),
  payment.method / payment.terms → top-level payment_method / payment_terms.
"""
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, AliasChoices, field_validator, model_validator

_BASE_CONFIG = ConfigDict(extra="ignore", populate_by_name=True, str_strip_whitespace=True)


class Seller(BaseModel):
    model_config = _BASE_CONFIG
    name: str | None = None
    address: str | None = None
    city: str | None = None
    country: str | None = None
    tax_id: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None


class Buyer(BaseModel):
    model_config = _BASE_CONFIG
    name: str | None = None
    address: str | None = None
    city: str | None = None
    country: str | None = None
    tax_id: str | None = None
    phone: str | None = None
    email: str | None = None
    contact: str | None = None


class ShipTo(BaseModel):
    model_config = _BASE_CONFIG
    name: str | None = None
    address: str | None = None
    city: str | None = None
    country: str | None = None
    contact: str | None = None


import re

def _clean_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        # Elimina símbolos de moneda, letras, y espacios
        cleaned = re.sub(r'[^\d.,-]', '', v)
        if not cleaned:
            return None
        # Si tiene coma y punto, asumimos punto como decimal
        if ',' in cleaned and '.' in cleaned:
            cleaned = cleaned.replace(',', '')
        # Si solo tiene coma, comprobamos si hay 3 digitos despues (probablemente miles)
        elif ',' in cleaned:
            parts = cleaned.split(',')
            if len(parts[-1]) == 3:
                cleaned = cleaned.replace(',', '')
            else:
                cleaned = cleaned.replace(',', '.')
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None

class Item(BaseModel):
    model_config = _BASE_CONFIG
    line_number: int | None = Field(default=None, validation_alias=AliasChoices("line_number", "item_number", "no"))
    reference_code: str | None = Field(default=None, validation_alias=AliasChoices("reference_code", "part_number", "code"))
    description: str | None = Field(default=None, validation_alias=AliasChoices("description", "product_name", "goods"))
    hs_code: str | None = None
    country_of_origin: str | None = None
    quantity: float | None = Field(default=None, validation_alias=AliasChoices("quantity", "qty"))
    unit_of_measure: str | None = Field(default=None, validation_alias=AliasChoices("unit_of_measure", "unit", "uom"))
    unit_price: float | None = Field(default=None, validation_alias=AliasChoices("unit_price", "price"))
    discount_amount: float | None = None
    discount_percent: float | None = None
    line_total: float | None = Field(default=None, validation_alias=AliasChoices("line_total", "total_price", "amount", "total"))
    currency: str | None = None
    purchase_order: str | None = None
    additional_attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("quantity", "unit_price", "line_total", mode="before")
    @classmethod
    def _parse_floats(cls, v):
        return _clean_float(v)


class TaxLine(BaseModel):
    model_config = _BASE_CONFIG
    label: str | None = None
    rate_percent: float | None = Field(default=None, validation_alias=AliasChoices("rate_percent", "rate"))
    amount: float | None = Field(default=None, validation_alias=AliasChoices("amount", "tax_amount"))

    @field_validator("rate_percent", "amount", mode="before")
    @classmethod
    def _parse_floats(cls, v):
        return _clean_float(v)


class Shipment(BaseModel):
    model_config = _BASE_CONFIG
    transport_mode: str | None = None
    vessel_or_flight: str | None = Field(
        default=None,
        validation_alias=AliasChoices("vessel_or_flight", "vessel", "flight"),
    )
    voyage_or_flight_number: str | None = Field(
        default=None,
        validation_alias=AliasChoices("voyage_or_flight_number", "voyage", "flight_number"),
    )
    port_of_loading: str | None = None
    port_of_discharge: str | None = None
    place_of_delivery: str | None = None
    country_of_origin: str | None = None
    country_of_destination: str | None = None
    bl_or_awb_number: str | None = Field(
        default=None,
        validation_alias=AliasChoices("bl_or_awb_number", "bl_number", "awb_number"),
    )
    container_numbers: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("container_numbers", "container_number", "containers"),
    )
    marks_and_numbers: str | None = None
    gross_weight_kg: float | None = None
    net_weight_kg: float | None = None
    volume_cbm: float | None = None
    package_count: int | None = None
    package_type: str | None = None

    @field_validator("gross_weight_kg", "net_weight_kg", "volume_cbm", mode="before")
    @classmethod
    def _parse_floats(cls, v):
        return _clean_float(v)

    @field_validator("container_numbers", mode="before")
    @classmethod
    def _coerce_container_numbers(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [v]
        return v


class BankInfo(BaseModel):
    model_config = _BASE_CONFIG
    beneficiary_name: str | None = None
    bank_name: str | None = None
    bank_address: str | None = None
    account_number: str | None = None
    iban: str | None = None
    swift_bic: str | None = None
    routing_number: str | None = None
    intermediary_bank: str | None = None


class References(BaseModel):
    model_config = _BASE_CONFIG
    purchase_order: str | None = None
    contract_number: str | None = Field(
        default=None,
        validation_alias=AliasChoices("contract_number", "contract"),
    )
    quote_number: str | None = None
    customer_reference: str | None = None
    other: list[str] = Field(default_factory=list)


class _PaymentBlock(BaseModel):
    """Bloque auxiliar para Qwen cuando anida payment.method/payment.terms."""
    model_config = _BASE_CONFIG
    method: str | None = None
    terms: str | None = None


class Factura(BaseModel):
    model_config = _BASE_CONFIG

    invoice_number: str | None = None
    invoice_type: str | None = None
    issue_date: str | None = None
    due_date: str | None = None
    shipment_date: str | None = None
    currency: str | None = None
    exchange_rate: float | None = None

    seller: Seller = Field(default_factory=Seller)
    buyer: Buyer = Field(default_factory=Buyer)
    ship_to: ShipTo = Field(default_factory=ShipTo)

    items: list[Item] = Field(
        default_factory=list,
        validation_alias=AliasChoices("items", "description_of_goods", "goods")
    )

    subtotal: float | None = None
    discount_total: float | None = None
    tax_breakdown: list[TaxLine] = Field(
        default_factory=list,
        validation_alias=AliasChoices("tax_breakdown", "tax", "taxes"),
    )
    freight_cost: float | None = Field(
        default=None,
        validation_alias=AliasChoices("freight_cost", "freight"),
    )
    insurance_cost: float | None = Field(
        default=None,
        validation_alias=AliasChoices("insurance_cost", "insurance"),
    )
    packaging_cost: float | None = Field(
        default=None,
        validation_alias=AliasChoices("packaging_cost", "packaging"),
    )
    other_charges: float | None = None
    total_amount: float | None = Field(
        default=None,
        validation_alias=AliasChoices("total_amount", "total"),
    )

    incoterm: str | None = Field(
        default=None,
        validation_alias=AliasChoices("incoterm", "incoterms"),
    )
    incoterm_location: str | None = Field(
        default=None,
        validation_alias=AliasChoices("incoterm_location", "incoterm_place", "incoterm_city"),
    )

    payment_terms: str | None = None
    payment_method: str | None = None

    shipment: Shipment = Field(
        default_factory=Shipment,
        validation_alias=AliasChoices("shipment", "shipping_details", "shipping")
    )
    bank_info: BankInfo = Field(default_factory=BankInfo)
    references: References = Field(default_factory=References)
    notes: str | None = None

    @field_validator("subtotal", "discount_total", "freight_cost", "insurance_cost", "packaging_cost", "other_charges", "total_amount", "exchange_rate", mode="before")
    @classmethod
    def _parse_floats(cls, v):
        return _clean_float(v)

    @field_validator("tax_breakdown", mode="before")
    @classmethod
    def _coerce_tax(cls, v):
        if v is None:
            return []
        return v

    @model_validator(mode="before")
    @classmethod
    def _unnest_payment_and_incoterm(cls, data):
        """Si Qwen produce estructuras anidadas erróneas, las aplana."""
        if isinstance(data, dict):
            # Payment
            if isinstance(data.get("payment"), dict):
                block = data["payment"]
                if data.get("payment_method") is None and block.get("method") is not None:
                    data["payment_method"] = block["method"]
                if data.get("payment_terms") is None and block.get("terms") is not None:
                    data["payment_terms"] = block["terms"]
            
            # Incoterm
            inc_key = "incoterms" if "incoterms" in data else "incoterm"
            inc = data.get(inc_key)
            if isinstance(inc, dict):
                if data.get("incoterm_location") is None and inc.get("place") is not None:
                    data["incoterm_location"] = str(inc.get("place"))
                data[inc_key] = str(inc.get("term")) if inc.get("term") is not None else None
                
        return data
