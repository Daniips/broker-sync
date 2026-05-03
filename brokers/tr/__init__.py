"""Trade Republic broker package.

Exports the CAPABILITIES set used by core.features to know what features
work for TR. When adding a new capability that TR supports, list it here
and the corresponding feature(s) in core/features.py become available.
"""

CAPABILITIES = frozenset({
    # Basic data
    "fetch_transactions",
    "fetch_snapshot",
    "fetch_price_history",

    # Event types TR emits that we consume
    "expense_tracking",   # CARD_TRANSACTION, PAYMENT_BIZUM_*, BANK_TRANSACTION_*
    "saveback",            # SAVEBACK_AGGREGATE — TR card perks
    "gifts",               # GIFTING_RECIPIENT_ACTIVITY, lottery prizes

    # Specific reports
    "tax_renta_es",        # FIFO + dividends in the Spanish IRPF format
})
