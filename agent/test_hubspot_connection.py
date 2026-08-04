"""One-off, standalone connectivity check for the HubSpot integration —
NOT part of any search. Creates/updates a single test Company record
directly, so you can confirm your token and both custom properties
(Outreach Status, Channel Methods Do Not Call) work against your real
HubSpot account without spending any Anthropic API budget or waiting on a
real lead.

Usage:
    python test_hubspot_connection.py
    (you'll be prompted for your token — typing it here isn't saved
    anywhere and doesn't end up in your shell history, unlike passing it
    as a command-line argument)

    Or, if you don't mind it in your shell history:
    python test_hubspot_connection.py YOUR_HUBSPOT_TOKEN [outreach_property] [no_call_property]

Safe to run repeatedly — it upserts the same test domain each time rather
than creating duplicates. Delete the "BD Agent Test Co" company in HubSpot
afterward if you don't want it cluttering your CRM.
"""

import getpass
import sys

import hubspot_sync

if len(sys.argv) > 1:
    api_key = sys.argv[1]
    outreach_property = sys.argv[2] if len(sys.argv) > 2 else hubspot_sync.DEFAULT_OUTREACH_PROPERTY
    no_call_property = sys.argv[3] if len(sys.argv) > 3 else hubspot_sync.DEFAULT_NO_COLD_CALL_PROPERTY
else:
    api_key = getpass.getpass("HubSpot access token (input hidden): ").strip()
    if not api_key:
        print("No token entered — exiting.")
        sys.exit(1)
    outreach_property = input(
        f"Outreach Status internal name [{hubspot_sync.DEFAULT_OUTREACH_PROPERTY}]: "
    ).strip() or hubspot_sync.DEFAULT_OUTREACH_PROPERTY
    no_call_property = input(
        f"Channel Methods Do Not Call internal name [{hubspot_sync.DEFAULT_NO_COLD_CALL_PROPERTY}]: "
    ).strip() or hubspot_sync.DEFAULT_NO_COLD_CALL_PROPERTY

print(f"Using outreach property: {outreach_property!r}")
print(f"Using no-call property: {no_call_property!r}")
print("Upserting test Company (domain: bdagent-test.example.com)...")

try:
    company_id = hubspot_sync.upsert_company(
        domain="bdagent-test.example.com",
        name="BD Agent Test Co",
        api_key=api_key,
        outreach_property=outreach_property,
        status=hubspot_sync.CONTACTED_VALUE,
        no_cold_call_property=no_call_property,
    )
    print(f"SUCCESS — Company created/updated, HubSpot ID: {company_id}")
    print('Go check HubSpot for a Company called "BD Agent Test Co" — its Outreach Status should say '
          '"Contacted" and Channel Methods Do Not Call should say "Yes".')
except hubspot_sync.HubSpotAPIError as exc:
    print(f"FAILED: {exc}")
    print("\nIf the error mentions a property not existing, double-check its internal name — "
          "it needs to be the Company object's internal name specifically, and the two properties "
          "(Outreach Status, Channel Methods Do Not Call) can have different internal names.")
    sys.exit(1)

print("\nNow checking is_company_declined() (should be False, nothing's marked Declined)...")
declined = hubspot_sync.is_company_declined("bdagent-test.example.com", api_key, outreach_property)
print(f"is_company_declined result: {declined}")
