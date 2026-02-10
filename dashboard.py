import customtkinter as ctk
import mysql.connector
from tkinter import messagebox
import threading
import pandas as pd
# pvlib
import pvlib
from pvlib.pvsystem import PVSystem
from pvlib.location import Location
from pvlib.modelchain import ModelChain
from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS
# Misc
import random
import time
import sys
import numpy as np
from datetime import datetime, timedelta

# Matplotlib
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib.dates as mdates
from typing import Any

# ==========================================
#   CONFIGURATION
# ==========================================
DB_CONFIG = {
    "host": "127.0.0.1",
    "user": "root",
    "password": "David2003!",
    "database": "solar_monitor"
}

LATITUDE = 45.6427
LONGITUDE = 25.5887
TZ = 'Europe/Bucharest'


# ==========================================
#   BATTERY LOGIC
# ==========================================
class BatterySystem:
    def __init__(self, capacity_kwh=10.0):
        self.capacity_kwh = capacity_kwh
        self.soc = 20.0
        self.max_charge_kw = 5.0
        self.efficiency = 0.95

    def update(self, power_available_kw, duration_hours):
        # 1. CHARGING
        if power_available_kw > 0:
            energy_needed_kwh = (100 - self.soc) / 100 * self.capacity_kwh
            charge_power = min(power_available_kw, self.max_charge_kw)
            energy_added = charge_power * duration_hours * self.efficiency

            if energy_added > energy_needed_kwh:
                energy_added = energy_needed_kwh
                charge_power = energy_added / (duration_hours * self.efficiency) if duration_hours > 0 else 0

            self.soc += (energy_added / self.capacity_kwh) * 100
            self.soc = min(100, self.soc)
            grid_export = power_available_kw - charge_power
            return charge_power, -grid_export

        # 2. DISCHARGING
        else:
            load_needed = abs(power_available_kw)
            energy_available_kwh = max(0, (self.soc - 5) / 100 * self.capacity_kwh)
            discharge_power = min(load_needed, self.max_charge_kw)
            energy_removed = discharge_power * duration_hours

            if energy_removed > energy_available_kwh:
                energy_removed = energy_available_kwh
                discharge_power = energy_removed / duration_hours if duration_hours > 0 else 0

            self.soc -= (energy_removed / self.capacity_kwh) * 100
            self.soc = max(5, self.soc)
            grid_import = load_needed - discharge_power
            return -discharge_power, grid_import


# ==========================================
#   SIMULATION ENGINE
# ==========================================
class ProsumerSim:
    def __init__(self):
        sandia_modules = pvlib.pvsystem.retrieve_sam('SandiaMod')
        cec_inverters = pvlib.pvsystem.retrieve_sam('cecinverter')
        mod_name = next((c for c in sandia_modules.columns if 'Canadian_Solar' in c), sandia_modules.columns[0])
        inv_name = next((c for c in cec_inverters.columns if 'SMA_America' in c), cec_inverters.columns[0])

        self.location = Location(LATITUDE, LONGITUDE, tz=TZ, altitude=600)
        system = PVSystem(
            surface_tilt=42,
            surface_azimuth=180,
            module_parameters=sandia_modules[mod_name],
            inverter_parameters=cec_inverters[inv_name],
            temperature_model_parameters=TEMPERATURE_MODEL_PARAMETERS['sapm']['open_rack_glass_glass'],
            modules_per_string=26,
            strings_per_inverter=1
        )
        self.mc = ModelChain(system, self.location)
        self.battery = BatterySystem(capacity_kwh=10.0)
        self.cloud_cover = 1.0

    @staticmethod
    def get_house_consumption(timestamp):
        hour = int(timestamp.hour)
        base_noise = random.uniform(-0.1, 0.1)

        if hour >= 23 or hour < 6:
            base_load = 0.2
        elif 6 <= hour < 9:
            base_load = 1.2 + random.uniform(0, 0.5)
        elif 9 <= hour < 17:
            base_load = 0.5
            if random.random() > 0.9:
                base_load += 1.8
        else:
            base_load = 2.5 + random.uniform(0, 1.0)

        return max(0.1, base_load + base_noise)

    def get_solar_production(self, timestamp):
        try:
            weather = self.location.get_clearsky(pd.DatetimeIndex([timestamp]))
            weather['temp_air'] = 10.0
            weather['wind_speed'] = 2.0
            self.mc.run_model(weather)

            ideal_power = self.mc.results.ac.iloc[0]
            if ideal_power < 10:
                return 0.0

            change = random.uniform(-0.1, 0.1)
            self.cloud_cover += change
            self.cloud_cover = max(0.2, min(1.0, self.cloud_cover))
            if random.random() > 0.8:
                self.cloud_cover = 1.0

            noisy_power = (ideal_power * self.cloud_cover) + random.uniform(-50, 50)
            return max(0, noisy_power / 1000)
        except:
            return 0.0


# ==========================================
#   MAIN APP
# ==========================================
class SolarApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.simulation_running = False
        self.selected_date = None
        self.sim_engine = ProsumerSim()
        self.graph_data = None
        self.is_hovering = False

        self.fig = None
        self.ax = None
        self.ax2 = None
        self.canvas = None
        self.toolbar = None
        self.cursor_line = None
        self.db_window = None
        self.prog_window = None

        self.title("Prosumer Monitor")
        self.geometry("1300x950")
        ctk.set_appearance_mode("Dark")

        # HEADER
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.header_frame.pack(pady=10, padx=20, fill="x")
        ctk.CTkLabel(self.header_frame, text="Smart Home Energy System", font=("Arial", 28, "bold")).pack(side="left")
        ctk.CTkButton(self.header_frame, text="Database Tools ⚙️", width=120, fg_color="#546E7A",
                      command=self.open_db_menu).pack(side="right")

        # STATS
        self.stats_frame = ctk.CTkFrame(self)
        self.stats_frame.pack(pady=10, padx=20, fill="x")
        self.stats_frame.columnconfigure((0, 1, 2, 3), weight=1)

        # 1. Solar
        self.f_solar = ctk.CTkFrame(self.stats_frame, fg_color="#1b5e20")
        self.f_solar.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        ctk.CTkLabel(self.f_solar, text="SOLAR GEN", font=("Arial", 12)).pack(pady=(10, 0))
        self.lbl_solar = ctk.CTkLabel(self.f_solar, text="-- kW", font=("Arial", 30, "bold"))
        self.lbl_solar.pack(pady=10)

        # 2. House
        self.f_house = ctk.CTkFrame(self.stats_frame, fg_color="#b71c1c")
        self.f_house.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        ctk.CTkLabel(self.f_house, text="HOUSE LOAD", font=("Arial", 12)).pack(pady=(10, 0))
        self.lbl_house = ctk.CTkLabel(self.f_house, text="-- kW", font=("Arial", 30, "bold"))
        self.lbl_house.pack(pady=10)

        # 3. Battery
        self.f_batt = ctk.CTkFrame(self.stats_frame, fg_color="#4a148c")
        self.f_batt.grid(row=0, column=2, padx=5, pady=5, sticky="ew")
        self.lbl_batt_title = ctk.CTkLabel(self.f_batt, text="BATTERY (IDLE)", font=("Arial", 12))
        self.lbl_batt_title.pack(pady=(10, 0))
        self.lbl_batt = ctk.CTkLabel(self.f_batt, text="-- %", font=("Arial", 30, "bold"))
        self.lbl_batt.pack(pady=10)

        # 4. Grid
        self.f_grid = ctk.CTkFrame(self.stats_frame, fg_color="#0d47a1")
        self.f_grid.grid(row=0, column=3, padx=5, pady=5, sticky="ew")
        self.lbl_grid_title = ctk.CTkLabel(self.f_grid, text="GRID STATUS", font=("Arial", 12))
        self.lbl_grid_title.pack(pady=(10, 0))
        self.lbl_grid = ctk.CTkLabel(self.f_grid, text="-- kW", font=("Arial", 30, "bold"))
        self.lbl_grid.pack(pady=10)

        # CONTROLS
        self.ctrl_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.ctrl_frame.pack(pady=(10, 0), padx=20, fill="x")

        ctk.CTkLabel(self.ctrl_frame, text="Date:", font=("Arial", 14, "bold")).pack(side="left", padx=(0, 10))
        self.date_combo = ctk.CTkComboBox(self.ctrl_frame, width=150, command=self.on_date_select)
        self.date_combo.pack(side="left")
        ctk.CTkButton(self.ctrl_frame, text="↻", width=30, command=self.populate_date_list).pack(side="left", padx=5)

        # Toggles
        self.var_show_batt = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self.ctrl_frame, text="Show Battery", variable=self.var_show_batt, command=self.update_graph,
                        fg_color="purple").pack(side="right", padx=10)
        self.var_show_grid = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self.ctrl_frame, text="Show Grid", variable=self.var_show_grid, command=self.update_graph,
                        fg_color="blue").pack(side="right", padx=10)
        self.var_show_house = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self.ctrl_frame, text="Show Load", variable=self.var_show_house, command=self.update_graph,
                        fg_color="red").pack(side="right", padx=10)
        self.var_show_solar = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self.ctrl_frame, text="Show Solar", variable=self.var_show_solar, command=self.update_graph,
                        fg_color="green").pack(side="right", padx=10)

        # GRAPH
        self.graph_frame = ctk.CTkFrame(self)
        self.graph_frame.pack(pady=5, padx=20, fill="both", expand=True)
        self.init_graph()

        # FOOTER
        self.footer_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.footer_frame.pack(pady=10, padx=20, fill="x")
        self.sim_switch = ctk.CTkSwitch(self.footer_frame, text="RUN LIVE SIM", command=self.toggle_simulation,
                                        progress_color="#00E676")
        self.sim_switch.pack(side="left")
        self.lbl_updated = ctk.CTkLabel(self.footer_frame, text="--", text_color="gray")
        self.lbl_updated.pack(side="left", padx=20)
        ctk.CTkButton(self.footer_frame, text="Exit", fg_color="gray", width=80, command=self.close_app).pack(
            side="right")

        self.populate_date_list()
        self.auto_refresh_data()
        self.auto_refresh_graph()

    # =========================================
    #  GRAPH & VIZ
    # =========================================
    def init_graph(self):
        plt.style.use('dark_background')
        self.fig, self.ax = plt.subplots(figsize=(6, 4), dpi=100)
        self.ax2 = self.ax.twinx()

        self.fig.patch.set_facecolor('#2b2b2b')
        self.ax.set_facecolor('#2b2b2b')
        self.ax2.set_ylabel("Battery %", color='violet', fontsize=9)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, self.graph_frame)
        self.toolbar.update()

        # CONNECT CLICKS
        self.canvas.mpl_connect("button_press_event", self.on_click)

        self.cursor_line = None
        self.is_hovering = False

    def on_click(self, event: Any):
        print(f"Click Detected! Button: {event.button}")

        if event.inaxes != self.ax and event.inaxes != self.ax2:
            print("Click was outside the graph axes.")
            return

        if self.graph_data is None or self.graph_data.empty:
            print("No data loaded to select.")
            return

        if event.button == 3:
            print("Resetting to Live Mode")
            self.is_hovering = False
            self.lbl_batt_title.configure(text="BATTERY")
            self.lbl_grid_title.configure(text="GRID STATUS")
            if self.cursor_line:
                self.cursor_line.remove()
                self.cursor_line = None
                self.canvas.draw()
            return

        if event.button == 1:
            try:
                self.is_hovering = True

                click_time = mdates.num2date(event.xdata).replace(tzinfo=None)
                print(f"Selected Time: {click_time}")

                nearest_idx = (self.graph_data['timestamp'] - click_time).abs().idxmin()
                row = self.graph_data.loc[nearest_idx]

                if self.cursor_line:
                    self.cursor_line.remove()

                self.cursor_line = self.ax.axvline(x=row['timestamp'], color='white', linestyle='--', linewidth=1)
                self.canvas.draw()

                self.lbl_solar.configure(text=f"{row['solar_kw']:.2f} kW")
                self.lbl_house.configure(text=f"{row['consumption_kw']:.2f} kW")

                batt_kw = row['battery_kw']
                batt_soc = row['battery_soc']
                if batt_kw > 0.01:
                    self.lbl_batt_title.configure(text="CHARGING")
                    self.lbl_batt.configure(text=f"{batt_soc:.0f}% (+{batt_kw:.1f}kW)")
                elif batt_kw < -0.01:
                    self.lbl_batt_title.configure(text="DISCHARGING")
                    self.lbl_batt.configure(text=f"{batt_soc:.0f}% ({batt_kw:.1f}kW)")
                else:
                    self.lbl_batt_title.configure(text="BATTERY (IDLE)")
                    self.lbl_batt.configure(text=f"{batt_soc:.0f}%")

                grid = row['grid_kw']
                t_str = row['timestamp'].strftime('%H:%M')
                if grid > 0.01:
                    self.lbl_grid_title.configure(text=f"IMPORTING @ {t_str}")
                    self.lbl_grid.configure(text=f"{grid:.2f} kW", text_color="#FF5252")
                elif grid < -0.01:
                    self.lbl_grid_title.configure(text=f"EXPORTING @ {t_str}")
                    self.lbl_grid.configure(text=f"{abs(grid):.2f} kW", text_color="#00E676")
                else:
                    self.lbl_grid_title.configure(text=f"GRID IDLE @ {t_str}")
                    self.lbl_grid.configure(text="0.00 kW", text_color="white")

            except Exception as e:
                print(f"Error processing click: {e}")

    def update_graph(self):
        if not self.selected_date or self.selected_date == "No Data" or self.selected_date == "No Data available":
            return

        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor()
            query = f"SELECT * FROM readings WHERE DATE(timestamp) = '{self.selected_date}' ORDER BY timestamp ASC"
            cursor.execute(query)
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            if rows:
                cols = ['id', 'timestamp', 'solar_kw', 'consumption_kw', 'grid_kw', 'battery_kw', 'battery_soc',
                        'voltage', 'current']
                df = pd.DataFrame(rows, columns=cols).drop(columns=['id'])
                for c in ['solar_kw', 'consumption_kw', 'grid_kw', 'battery_kw', 'battery_soc']:
                    df[c] = df[c].astype(float)

                df['x_num'] = mdates.date2num(df['timestamp'])

                self.graph_data = df
            else:
                return

            self.ax.clear()
            self.ax2.clear()
            self.cursor_line = None

            self.ax.grid(True, linestyle='--', alpha=0.3)

            # LAYERS
            if self.var_show_solar.get():
                self.ax.fill_between(df['timestamp'], 0, df['solar_kw'], color='#00E676', alpha=0.15,
                                     label='_nolegend_')

            if self.var_show_grid.get() and self.var_show_solar.get() and self.var_show_house.get():
                self.ax.fill_between(df['timestamp'], df['consumption_kw'], df['solar_kw'],
                                     where=(df['solar_kw'] > df['consumption_kw']),
                                     interpolate=True, color='green', alpha=0.4, label='Export')
                self.ax.fill_between(df['timestamp'], df['consumption_kw'], df['solar_kw'],
                                     where=(df['consumption_kw'] > df['solar_kw']),
                                     interpolate=True, color='red', alpha=0.4, label='Import')

            if self.var_show_solar.get():
                self.ax.plot(df['timestamp'], df['solar_kw'], color='#00E676', label='Solar', lw=1.5)
            if self.var_show_house.get():
                self.ax.plot(df['timestamp'], df['consumption_kw'], color='#FF5252', label='House', lw=1.5)
            if self.var_show_batt.get():
                self.ax2.plot(df['timestamp'], df['battery_soc'], color='violet', linestyle=':', label='SOC %', lw=1.5)
                self.ax2.fill_between(df['timestamp'], df['battery_soc'], color='violet', alpha=0.05)
                self.ax2.set_ylim(0, 105)

            if self.ax.get_legend_handles_labels()[0]:
                self.ax.legend(loc='upper left')
            if self.ax2.get_legend_handles_labels()[0]:
                self.ax2.legend(loc='upper right')

            self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
            self.fig.autofmt_xdate()
            self.canvas.draw()
        except Exception as e:
            print(f"Graph Error: {e}")

    # =========================================
    #  LOGIC
    # =========================================
    def auto_refresh_data(self):
        if not self.is_hovering:
            try:
                conn = mysql.connector.connect(**DB_CONFIG)
                cur = conn.cursor()
                cur.execute(
                    "SELECT timestamp, solar_kw, consumption_kw, grid_kw, battery_kw, battery_soc FROM readings ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                conn.close()
                if row:
                    ts, sol, house, grid, batt_kw, batt_soc = row
                    self.lbl_solar.configure(text=f"{float(sol):.2f} kW")
                    self.lbl_house.configure(text=f"{float(house):.2f} kW")
                    self.lbl_updated.configure(text=f"Updated: {ts}")

                    if float(batt_kw) > 0.01:
                        self.lbl_batt_title.configure(text="CHARGING")
                        self.lbl_batt.configure(text=f"{float(batt_soc):.0f}% (+{float(batt_kw):.1f}kW)")
                    elif float(batt_kw) < -0.01:
                        self.lbl_batt_title.configure(text="DISCHARGING")
                        self.lbl_batt.configure(text=f"{float(batt_soc):.0f}% ({float(batt_kw):.1f}kW)")
                    else:
                        self.lbl_batt_title.configure(text="BATTERY (IDLE)")
                        self.lbl_batt.configure(text=f"{float(batt_soc):.0f}%")

                    if float(grid) > 0.01:
                        self.lbl_grid_title.configure(text="IMPORTING")
                        self.lbl_grid.configure(text=f"{float(grid):.2f} kW", text_color="#FF5252")
                    elif float(grid) < -0.01:
                        self.lbl_grid_title.configure(text="EXPORTING")
                        self.lbl_grid.configure(text=f"{abs(float(grid)):.2f} kW", text_color="#00E676")
                    else:
                        self.lbl_grid_title.configure(text="GRID IDLE")
                        self.lbl_grid.configure(text="0.00 kW", text_color="white")
            except:
                pass
        self.after(2000, self.auto_refresh_data)

    def auto_refresh_graph(self):
        today = datetime.now().strftime('%Y-%m-%d')
        if self.selected_date == today and not self.is_hovering: self.update_graph()
        self.after(10000, self.auto_refresh_graph)

    def toggle_simulation(self):
        if self.sim_switch.get() == 1:
            self.simulation_running = True
            threading.Thread(target=self.run_simulation_loop, daemon=True).start()
        else:
            self.simulation_running = False

    def run_simulation_loop(self):
        while self.simulation_running:
            try:
                now = pd.Timestamp.now(tz=TZ)
                solar = self.sim_engine.get_solar_production(now)
                house = self.sim_engine.get_house_consumption(now)
                net = solar - house
                batt_kw, grid_kw = self.sim_engine.battery.update(net, duration_hours=5 / 3600)
                volt = 230.0 + random.uniform(-1, 1)
                curr = 0
                conn = mysql.connector.connect(**DB_CONFIG)
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO readings (timestamp, solar_kw, consumption_kw, grid_kw, battery_kw, battery_soc, voltage, current) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (now.strftime('%Y-%m-%d %H:%M:%S'), solar, house, grid_kw, batt_kw, self.sim_engine.battery.soc,
                     volt, curr))
                conn.commit()
                conn.close()
                time.sleep(5)
            except Exception as e:
                print(e)
                time.sleep(5)

    def open_db_menu(self):

        self.db_window = ctk.CTkToplevel(self)
        self.db_window.title("Database Tools")
        self.db_window.geometry("300x300")
        self.db_window.attributes("-topmost", True)

        ctk.CTkButton(self.db_window, text="RE-INITIALIZE TABLE", fg_color="red", command=self.reinit_table).pack(
            pady=20, padx=20)
        ctk.CTkButton(self.db_window, text="Generate History", command=self.ask_generate_history).pack(pady=10, padx=20)
        ctk.CTkButton(self.db_window, text="Clear Data", fg_color="orange", command=self.clear_data).pack(pady=10,
                                                                                                          padx=20)

    def reinit_table(self):
        if messagebox.askyesno("Re-Init", "Drop table?"):
            conn = mysql.connector.connect(**DB_CONFIG)
            cur = conn.cursor()
            cur.execute("DROP TABLE IF EXISTS readings")
            cur.execute(
                "CREATE TABLE readings (id BIGINT AUTO_INCREMENT PRIMARY KEY, timestamp DATETIME NOT NULL, solar_kw DECIMAL(10,3), consumption_kw DECIMAL(10,3), grid_kw DECIMAL(10,3), battery_kw DECIMAL(10,3), battery_soc DECIMAL(5,2), voltage DECIMAL(6,2), current DECIMAL(6,2), INDEX(timestamp))")
            conn.commit()
            conn.close()
            messagebox.showinfo("Done", "Table Updated.")
            self.reset_ui()

    def ask_generate_history(self):
        d = ctk.CTkInputDialog(text="Days:", title="Backfill").get_input()

        if d and d.isdigit():
            self.show_progress_window(int(d))
            threading.Thread(target=self.run_backfill, args=(int(d),)).start()

    def show_progress_window(self, total_days):
        self.prog_window = ctk.CTkToplevel(self)
        self.prog_window.title("Generating...")
        self.prog_window.geometry("300x150")
        self.prog_window.attributes("-topmost", True)
        self.lbl_prog = ctk.CTkLabel(self.prog_window, text="Initializing...")
        self.lbl_prog.pack(pady=20)
        self.progress_bar = ctk.CTkProgressBar(self.prog_window, width=200)
        self.progress_bar.pack(pady=10)
        self.progress_bar.set(0)

    def run_backfill(self, days):
        try:
            print(f"Starting backfill for {days} days...")
            end = pd.Timestamp.now(tz=TZ)
            start = end - pd.Timedelta(days=days)
            times = pd.date_range(start, end, freq='15min')

            total = len(times)
            processed = 0

            conn = mysql.connector.connect(**DB_CONFIG)
            cur = conn.cursor()
            q = "INSERT INTO readings (timestamp, solar_kw, consumption_kw, grid_kw, battery_kw, battery_soc, voltage, current) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"

            chunk = []
            # Reset battery for simulation
            self.sim_engine.battery.soc = 20.0

            for t in times:
                sol = self.sim_engine.get_solar_production(t)
                house = self.sim_engine.get_house_consumption(t)
                net = sol - house
                batt_kw, grid_kw = self.sim_engine.battery.update(net, duration_hours=0.25)

                chunk.append((
                    t.strftime('%Y-%m-%d %H:%M:%S'),
                    sol, house, grid_kw, batt_kw,
                    self.sim_engine.battery.soc, 230.0, 0
                ))

                processed += 1

                if processed % 50 == 0:
                    self.progress_bar.set(processed / total)
                    self.lbl_prog.configure(text=f"Generated {processed}/{total}")

                if len(chunk) >= 5000:
                    cur.executemany(q, chunk)
                    conn.commit()
                    chunk = []

            if chunk:
                cur.executemany(q, chunk)
                conn.commit()

            conn.close()
            self.prog_window.destroy()
            messagebox.showinfo("Success", f"Generated {total} records!")
            self.populate_date_list()

        except Exception as e:
            print(f"Backfill Error: {e}")
            try:
                self.prog_window.destroy()
            except:
                pass

    def clear_data(self):

        confirm = messagebox.askyesno("Delete Data",
                                      "Are you sure you want to delete ALL data?\nThis cannot be undone.")

        if confirm:
            try:
                conn = mysql.connector.connect(**DB_CONFIG)
                cur = conn.cursor()
                cur.execute("TRUNCATE TABLE readings")
                conn.commit()
                conn.close()

                messagebox.showinfo("Success", "Database has been cleared.")

                if hasattr(self, 'db_window') and self.db_window.winfo_exists():
                    self.db_window.destroy()

                self.reset_ui()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to clear data: {e}")

    def reset_ui(self):
        self.selected_date = None
        self.date_combo.set("")
        self.ax.clear()
        self.ax2.clear()
        self.canvas.draw()
        self.populate_date_list()

    def populate_date_list(self):
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT DATE(timestamp) FROM readings ORDER BY DATE(timestamp) DESC")
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            if rows:
                dates = [str(r[0]) for r in rows]
                self.date_combo.configure(values=dates)
                if not self.selected_date or self.selected_date == "No Data available":
                    self.date_combo.set(dates[0])
                    self.selected_date = dates[0]
            else:
                # LIST IS EMPTY
                msg = "No Data available"
                self.date_combo.configure(values=[msg])
                self.date_combo.set(msg)
                self.selected_date = None

        except Exception as e:
            print(f"Date List Error: {e}")

    def on_date_select(self, c):
        self.selected_date = c
        self.update_graph()

    def close_app(self):
        self.simulation_running = False
        self.destroy()
        sys.exit()


if __name__ == "__main__":
    app = SolarApp()
    app.mainloop()
