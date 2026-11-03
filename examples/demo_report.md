# OEM Intelligence Agent — demo report

Data: entirely synthetic. No live market lookup.
Cases: 6 | Aged threshold: 90 days | Mode: rules

## #3 · Faro delantero — HIGH
OEM: `DEMO-LMP-3003` | Stock: 3 | Age: 185 days
Reasons: aged_inventory, oem_needs_verification
Market: 1 listings; 1 listed units; min EUR 180.0; max EUR 180.0
Flags: OEM is unverified; market matches are exploratory, not validated
Action: human review only.

## #7 · Electroventilador — HIGH
OEM: `DEMO-FAN-7007` | Stock: 2 | Age: 47 days
Reasons: oem_needs_verification
Market: 0 listings; 0 listed units; min EUR sin datos; max EUR sin datos
Flags: OEM is unverified; market matches are exploratory, not validated; No exact OEM market offers in the synthetic fixture
Action: human review only.

## #8 · Sensor de presion — HIGH
OEM: `DEMO-SEN-8008` | Stock: 1 | Age: 201 days
Reasons: aged_inventory
Market: 0 listings; 0 listed units; min EUR sin datos; max EUR sin datos
Flags: No exact OEM market offers in the synthetic fixture
Action: human review only.

## #5 · Motor de arranque — LOW
OEM: `DEMO-STA-5005` | Stock: 2 | Age: 155 days
Reasons: aged_inventory
Market: 2 listings; 3 listed units; min EUR 78.0; max EUR 90.0
Flags: none
Action: human review only.

## #1 · Alternador — MEDIUM
OEM: `DEMO-ALT-1001` | Stock: 2 | Age: 125 days
Reasons: aged_inventory
Market: 3 listings; 6 listed units; min EUR 95.0; max EUR 125.0
Flags: Current price exceeds highest observed comparable offer
Action: human review only.

## #4 · Cremallera direccion — MEDIUM
OEM: `DEMO-STR-4004` | Stock: 1 | Age: 99 days
Reasons: aged_inventory
Market: 2 listings; 3 listed units; min EUR 275.0; max EUR 315.0
Flags: none
Action: human review only.
