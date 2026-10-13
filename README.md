# Household PV & Battery Energy Storage Simulation

A Python-based simulation tool designed to model the energy flow of a residential photovoltaic (PV) prosumer system. The simulation allows users to configure and adjust solar generation parameters, household electricity consumption profiles, and battery storage capacity to evaluate energy self-sufficiency and grid interaction.

## Key Features

* **Solar PV Generation Modeling:** Simulate power output based on installed panel capacity and solar irradiance profiles.
* **Household Load Simulation:** Model daily and seasonal residential energy consumption patterns.
* **Battery Energy Storage System (BESS):** Configure battery capacity, State of Charge (SoC) limits, and charge/discharge logic to store excess solar energy and offset peak evening demand.
* **Grid Interaction Analysis:** Track surplus energy exported to the grid versus deficit energy imported when solar generation and battery reserves are depleted.
* **Interactive Parameter Tuning:** Easily modify system sizing variables in the script to test different configurations and optimize self-consumption.

## Configurable Parameters

Users can adjust the following core variables within the simulation:
* **PV Array Size:** Installed peak solar power ($kW_p$) and system efficiency.
* **Battery Storage:** Total usable capacity ($kWh$), maximum charge/discharge rates, and initial State of Charge (%).
* **Consumption Profile:** Base load and variable household power demand over time ($kW$).
