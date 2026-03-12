import os
import csv
import json
import threading
import customtkinter as ctk
#import errno
from tkinter import filedialog, messagebox
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
#import time
import re

try:
    import numbers_c2pa
    NUMBERS_C2PA_AVAILABLE = True
except Exception:
    NUMBERS_C2PA_AVAILABLE = False
    print("C2PA import error:", e)

CONFIG_FILE = "exif_gui_config.json"

# ---------- EXIF helpers ----------

def get_exif_dict(img_path):
    img = Image.open(img_path)
    exif_raw = img._getexif() or {}
    exif = {}
    for tag_id, value in exif_raw.items():
        tag = TAGS.get(tag_id, tag_id)
        if tag == "GPSInfo":
            gps_data = {}
            for t in value:
                sub_tag = GPSTAGS.get(t, t)
                gps_data[sub_tag] = value[t]
            exif["GPSInfo"] = gps_data
        else:
            exif[tag] = value
    return img, exif

def dms_to_decimal(dms, ref):
    if not dms or not ref:
        return None
    def frac_to_float(frac):
        try:
            return float(frac)
        except TypeError:
            return float(frac[0]) / float(frac[1])
    d = frac_to_float(dms[0])
    m = frac_to_float(dms[1])
    s = frac_to_float(dms[2])
    sign = -1 if ref in ["S", "W"] else 1
    return sign * (d + m / 60.0 + s / 3600.0)

def get_gps(exif):
    gps = exif.get("GPSInfo", {})
    lat = gps.get("GPSLatitude")
    lat_ref = gps.get("GPSLatitudeRef")
    lon = gps.get("GPSLongitude")
    lon_ref = gps.get("GPSLongitudeRef")
    lat_dec = dms_to_decimal(lat, lat_ref) if lat and lat_ref else None
    lon_dec = dms_to_decimal(lon, lon_ref) if lon and lon_ref else None
    return lat_dec, lon_dec, gps

def get_altitude(gps):
    alt = gps.get("GPSAltitude")
    alt_ref = gps.get("GPSAltitudeRef")
    if alt is None:
        return None
    try:
        val = float(alt)
    except TypeError:
        val = float(alt[0]) / float(alt[1])
    if alt_ref == 1:
        val = -val
    return val

def get_datetime(exif):
    dt = exif.get("DateTimeOriginal") or exif.get("DateTime")
    if not dt:
        return None, None
    try:
        date_str, time_str = dt.split(" ")
        date_str = date_str.replace(":", "-")
        return date_str, time_str
    except ValueError:
        return dt, None

def get_camera_model(exif):
    make = exif.get("Make")
    model = exif.get("Model")
    if make and model:
        return f"{make} {model}"
    return model or make or None

def to_float_maybe_rational(value):
    try:
        return float(value)
    except TypeError:
        return float(value[0]) / float(value[1])

def get_focal_lengths(exif):
    focal_mm = None
    focal_tag = exif.get("FocalLength")
    if focal_tag is not None:
        focal_mm = to_float_maybe_rational(focal_tag)
    focal_35_tag = exif.get("FocalLengthIn35mmFilm")
    focal_35mm = None
    if focal_35_tag is not None:
        focal_35mm = to_float_maybe_rational(focal_35_tag)
    return focal_mm, focal_35mm

def get_exposure_settings(exif):
    iso = exif.get("ISOSpeedRatings") or exif.get("PhotographicSensitivity")
    exposure_time = exif.get("ExposureTime")
    shutter = None
    if exposure_time is not None:
        try:
            val = to_float_maybe_rational(exposure_time)
            if val > 0:
                if val < 1:
                    shutter = f"1/{int(round(1 / val))} s"
                else:
                    shutter = f"{val:.3f} s"
            else:
                shutter = str(exposure_time)
        except Exception:
            shutter = str(exposure_time)
    fnumber = exif.get("FNumber")
    aperture = None
    if fnumber is not None:
        try:
            f_val = to_float_maybe_rational(fnumber)
            aperture = f"f/{f_val:.1f}"
        except Exception:
            aperture = str(fnumber)
    return iso, shutter, aperture

def get_lens_model(exif):
    return exif.get("LensModel")

def get_color_depth(img, exif):
    bits = exif.get("BitsPerSample")
    if isinstance(bits, (list, tuple)):
        try:
            return int(bits[0])
        except Exception:
            pass
    if isinstance(bits, (int, float)):
        return int(bits)
    mode = img.mode
    if mode in ("RGB", "RGBA", "L"):
        return 8
    return None

# ---------- Reverse geocoding ----------

geolocator = Nominatim(user_agent="exif_gui_app")
reverse = RateLimiter(geolocator.reverse, min_delay_seconds=1.1, max_retries=2, error_wait_seconds=5.0)

def reverse_geocode(lat, lon):
    if lat is None or lon is None:
        return None
    try:
        location = reverse((lat, lon), language="en", exactly_one=True)
        if location is None:
            return None
        addr = location.raw.get("address", {})
        place = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet")
        state = addr.get("state") or addr.get("county")
        country = addr.get("country")
        parts = [p for p in [place, state, country] if p]
        if parts:
            return ", ".join(parts)
        return location.address
    except Exception:
        return None

# ---------- C2PA helper ----------

def check_c2pa_status(path):
    if not NUMBERS_C2PA_AVAILABLE:
        return "C2PA: engine not installed"

    try:
        report = numbers_c2pa.read_c2pa_file(path)
        if not report:
            return "C2PA: no manifest"

        state = report.get("validation_state") or report.get("validation_status") or "Unknown"
        owner = extract_owner_from_manifest(report)
        ai_inf, ai_train = extract_ai_use_flags(report)

        parts = [f"state={state}"]

        if owner:
            parts.append(f"owner={owner}")

        if ai_inf:
            parts.append(f"AI inference use={ai_inf}")
        if ai_train:
            parts.append(f"AI training use={ai_train}")

        return "C2PA: " + ", ".join(parts)
    except OSError as e:
        if getattr(e, "winerror", None) == 2:
            return "C2PA: c2patool not installed or not on PATH"
        return f"C2PA: OS error ({e})"
    except Exception as e:
        return f"C2PA: error ({e})"
    
def extract_owner_from_manifest(report):
    try:
        active = report.get("active_manifest")
        if not active:
            return None
        manifest = report["manifests"][active]
        assertions = manifest.get("assertions", [])

        identity_data = None
        for a in assertions:
            if a.get("label") == "cawg.identity":
                identity_data = a.get("data", {})
                break
        if not identity_data:
            return None

        vis = identity_data.get("verifiedIdentities", [])
        # Prefer a document_verification entry with a name
        for v in vis:
            if v.get("type") == "cawg.document_verification" and v.get("name"):
                return v["name"]
        # Fallback to any username
        for v in vis:
            if v.get("username"):
                return v["username"]
        return None
    except Exception:
        return None

def extract_ai_use_flags(report):
    try:
        active = report.get("active_manifest")
        if not active:
            return None, None
        manifest = report["manifests"][active]
        assertions = manifest.get("assertions", [])

        ai_inf = None
        ai_train = None

        for a in assertions:
            if a.get("label") in ("c2pa.training-mining", "cawg.training-mining"):
                entries = a.get("data", {}).get("entries", {})
                inf = entries.get("c2pa.ai_inference") or entries.get("cawg.ai_inference")
                trn = entries.get("c2pa.ai_generative_training") or entries.get("cawg.ai_generative_training")
                if inf and "use" in inf:
                    ai_inf = inf["use"]
                if trn and "use" in trn:
                    ai_train = trn["use"]
        return ai_inf, ai_train
    except Exception:
        return None, None
    
# ---------- Send to Perplexity helper ----------

#def _find_prompt_box(driver):
    # Try main textarea first
    #textareas = driver.find_elements(By.TAG_NAME, "textarea")
    #if textareas:
    #    return textareas[0]
    # Fallback: any contenteditable area
    #inputs = driver.find_elements(By.CSS_SELECTOR, "[contenteditable='true']")
    #if inputs:
    #    return inputs[0]
    #raise RuntimeError("Could not find Perplexity prompt input box")

#def send_prompt_to_perplexity(prompt: str):
    # Start Comet via Selenium
    # comet_path = r"C:\Users\felip\AppData\Local\Perplexity\Comet\Application\comet.exe"  # <-- put your real path here
    # chrome_options = Options()
    # chrome_options.binary_location = comet_path
    
    # driver = webdriver.Chrome(options=chrome_options)
    # driver.get("https://www.perplexity.ai")
    # Wait for page & scripts to load; adjust if needed
    # time.sleep(5)

    # 1) Find box and focus it
    # box = _find_prompt_box(driver)
    # box.click()
    # box.clear()

    # 2) Type prompt slowly so Comet can update without losing everything
    #for ch in prompt:
    #    try:
    #        box.send_keys(ch)
    #    except Exception:
    #        # If element went stale, re‑find it and continue
    #        box = _find_prompt_box(driver)
    #        box.click()
    #        box.send_keys(ch)
    #    time.sleep(0.01)  # adjust speed if needed    

    # box.send_keys(prompt)


# ---------- Config helpers ----------

def load_config():
    if not os.path.isfile(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

# ---------- GUI (CustomTkinter) ----------

class ExifGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.body_font = ctk.CTkFont(family="Segoe UI", size=13)
        self.header_font = ctk.CTkFont(family="Segoe UI", size=14, weight="bold")

        self.title("getEXIF - EXIF data extractor for photos")
        # self.geometry("900x600")
        self.minsize(800, 500)

        self.file_path = ctk.StringVar()
        self.preview_img = None

        self.var_show_filename = ctk.BooleanVar()
        self.var_gps = ctk.BooleanVar()
        self.var_location = ctk.BooleanVar()
        self.var_altitude = ctk.BooleanVar()
        self.var_date = ctk.BooleanVar()
        self.var_time = ctk.BooleanVar()
        self.var_camera = ctk.BooleanVar()
        self.var_focal = ctk.BooleanVar()
        self.var_focal35 = ctk.BooleanVar()
        self.var_iso = ctk.BooleanVar()
        self.var_shutter = ctk.BooleanVar()
        self.var_aperture = ctk.BooleanVar()
        self.var_lens = ctk.BooleanVar()
        self.var_size = ctk.BooleanVar()
        self.var_color_depth = ctk.BooleanVar()
        self.var_csv = ctk.BooleanVar()
        self.var_c2pa = ctk.BooleanVar()

        self.load_checkbox_state()
        self.create_widgets()

    def create_widgets(self):
        label = ctk.CTkLabel(self, text="Choose a photo (JPG/JPEG):", font=self.body_font)
        label.grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 0), sticky="w")

        path_frame = ctk.CTkFrame(self, fg_color="transparent")
        path_frame.grid(row=1, column=0, columnspan=2, padx=10, pady=5, sticky="we")
        path_frame.grid_columnconfigure(0, weight=1)

        entry = ctk.CTkEntry(path_frame, textvariable=self.file_path, font=self.body_font)
        entry.grid(row=0, column=0, padx=(0, 5), pady=0, sticky="we")

        browse_btn = ctk.CTkButton(path_frame, text="Browse...", command=self.browse_file,
                                   width=80, font=self.body_font)
        browse_btn.grid(row=0, column=1, padx=0, pady=0, sticky="e")

        groups_frame = ctk.CTkFrame(self, fg_color="transparent")
        groups_frame.grid(row=2, column=0, columnspan=2, padx=10, pady=5, sticky="nw")
        for col in range(4):
            groups_frame.grid_columnconfigure(col, weight=0 if col < 3 else 1)

        # Row1: Image properties (col 0), Image settings (col 1), Date & time (col 2)
        frame_props = ctk.CTkFrame(groups_frame, border_width=1, corner_radius=8)
        frame_props.grid(row=0, column=0, padx=5, pady=5, sticky="nw")
        ctk.CTkLabel(frame_props, text="Image properties", font=self.header_font).grid(
            row=0, column=0, padx=5, pady=(5, 2), sticky="w"
        )
        ctk.CTkCheckBox(frame_props, text="Show filename in results",
                        variable=self.var_show_filename,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=1, column=0, padx=5, pady=2, sticky="w")
        ctk.CTkCheckBox(frame_props, text="Image size",
                        variable=self.var_size,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=2, column=0, padx=5, pady=2, sticky="w")
        ctk.CTkCheckBox(frame_props, text="Color depth (bits/channel)",
                        variable=self.var_color_depth,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=3, column=0, padx=5, pady=2, sticky="w")

        frame_settings = ctk.CTkFrame(groups_frame, border_width=1, corner_radius=8)
        frame_settings.grid(row=0, column=1, padx=5, pady=5, sticky="nw")
        ctk.CTkLabel(frame_settings, text="Image settings", font=self.header_font).grid(
            row=0, column=0, padx=5, pady=(5, 2), sticky="w"
        )
        ctk.CTkCheckBox(frame_settings, text="ISO",
                        variable=self.var_iso,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=1, column=0, padx=5, pady=2, sticky="w")
        ctk.CTkCheckBox(frame_settings, text="Shutter speed",
                        variable=self.var_shutter,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=2, column=0, padx=5, pady=2, sticky="w")
        ctk.CTkCheckBox(frame_settings, text="Aperture (f/)",
                        variable=self.var_aperture,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=3, column=0, padx=5, pady=2, sticky="w")

        frame_dt = ctk.CTkFrame(groups_frame, border_width=1, corner_radius=8)
        frame_dt.grid(row=0, column=2, padx=5, pady=5, sticky="nw")
        ctk.CTkLabel(frame_dt, text="Date & time", font=self.header_font).grid(
            row=0, column=0, padx=5, pady=(5, 2), sticky="w"
        )
        ctk.CTkCheckBox(frame_dt, text="Date",
                        variable=self.var_date,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=1, column=0, padx=5, pady=2, sticky="w")
        ctk.CTkCheckBox(frame_dt, text="Time",
                        variable=self.var_time,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=2, column=0, padx=5, pady=2, sticky="w")

        # Row2: GPS (col 0), Camera (col 1), Output (col 2)
        frame_gps = ctk.CTkFrame(groups_frame, border_width=1, corner_radius=8)
        frame_gps.grid(row=1, column=0, padx=5, pady=5, sticky="nw")
        ctk.CTkLabel(frame_gps, text="GPS data", font=self.header_font).grid(
            row=0, column=0, padx=5, pady=(5, 2), sticky="w"
        )
        ctk.CTkCheckBox(frame_gps, text="GPS (lat/lon)",
                        variable=self.var_gps,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=1, column=0, padx=5, pady=2, sticky="w")
        ctk.CTkCheckBox(frame_gps, text="Altitude",
                        variable=self.var_altitude,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=2, column=0, padx=5, pady=2, sticky="w")
        ctk.CTkCheckBox(frame_gps, text="Location (reverse geocode)",
                        variable=self.var_location,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=3, column=0, padx=5, pady=2, sticky="w")

        frame_camera = ctk.CTkFrame(groups_frame, border_width=1, corner_radius=8)
        frame_camera.grid(row=1, column=1, padx=5, pady=5, sticky="nw")
        ctk.CTkLabel(frame_camera, text="Camera information", font=self.header_font).grid(
            row=0, column=0, padx=5, pady=(5, 2), sticky="w"
        )
        ctk.CTkCheckBox(frame_camera, text="Camera model",
                        variable=self.var_camera,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=1, column=0, padx=5, pady=2, sticky="w")
        ctk.CTkCheckBox(frame_camera, text="Lens model",
                        variable=self.var_lens,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=2, column=0, padx=5, pady=2, sticky="w")
        ctk.CTkCheckBox(frame_camera, text="Focal length",
                        variable=self.var_focal,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=3, column=0, padx=5, pady=2, sticky="w")
        ctk.CTkCheckBox(frame_camera, text="35mm equivalent",
                        variable=self.var_focal35,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=4, column=0, padx=5, pady=2, sticky="w")

        frame_csv = ctk.CTkFrame(groups_frame, border_width=1, corner_radius=8)
        frame_csv.grid(row=1, column=2, padx=5, pady=5, sticky="nw")
        ctk.CTkLabel(frame_csv, text="Output", font=self.header_font).grid(
            row=0, column=0, padx=5, pady=(5, 2), sticky="w"
        )
        ctk.CTkCheckBox(frame_csv, text="Check for C2PA",
                        variable=self.var_c2pa,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=1, column=0, padx=5, pady=2, sticky="w")
        ctk.CTkCheckBox(frame_csv, text="Write CSV file",
                        variable=self.var_csv,
                        command=self.save_checkbox_state,
                        font=self.body_font).grid(row=2, column=0, padx=5, pady=2, sticky="w")

        # Preview as fourth column (col 3), spanning both rows
        preview_frame = ctk.CTkFrame(groups_frame, border_width=1, corner_radius=8)
        preview_frame.grid(row=0, column=3, rowspan=2, padx=10, pady=5, sticky="nsew")
        ctk.CTkLabel(preview_frame, text="Preview", font=self.header_font).grid(
            row=0, column=0, padx=5, pady=(5, 2), sticky="w"
        )
        self.preview_label = ctk.CTkLabel(preview_frame, text="")
        self.preview_label.grid(row=1, column=0, padx=5, pady=5, sticky="n")

        run_btn = ctk.CTkButton(self, text="Run", command=self.run_extraction, font=self.body_font)
        run_btn.grid(row=3, column=0, padx=20, pady=10, sticky="w")

        # Prompt button for sending to Perplexity
        self.prompt_btn = ctk.CTkButton(self, text="Create AI Prompt", command=self.on_send_to_perplexity, font=self.body_font, state="disabled")
        self.prompt_btn.grid(row=3, column=0, padx=180, pady=10, sticky="w")

        self.status_label = ctk.CTkLabel(self, text="Ready", font=self.body_font)
        self.status_label.grid(row=4, column=0, columnspan=2, padx=20, pady=5, sticky="w")

        ctk.CTkLabel(self, text="Extracted information:", font=self.body_font).grid(
            row=5, column=0, padx=10, pady=(10, 0), sticky="w"
        )
        self.info_text = ctk.CTkTextbox(self, width=800, height=200, wrap="word",
                                        font=("Segoe UI", 12))
        self.info_text.grid(row=6, column=0, columnspan=2, padx=10, pady=5, sticky="nsew")

        self.grid_rowconfigure(6, weight=1)
        self.grid_columnconfigure(0, weight=1)

    # ----- config -----

    def load_checkbox_state(self):
        cfg = load_config()
        self.var_show_filename.set(cfg.get("show_filename", True))
        self.var_gps.set(cfg.get("gps", True))
        self.var_location.set(cfg.get("location", True))
        self.var_altitude.set(cfg.get("altitude", True))
        self.var_date.set(cfg.get("date", True))
        self.var_time.set(cfg.get("time", True))
        self.var_camera.set(cfg.get("camera", True))
        self.var_focal.set(cfg.get("focal", True))
        self.var_focal35.set(cfg.get("focal35", True))
        self.var_iso.set(cfg.get("iso", True))
        self.var_shutter.set(cfg.get("shutter", True))
        self.var_aperture.set(cfg.get("aperture", True))
        self.var_lens.set(cfg.get("lens", True))
        self.var_size.set(cfg.get("size", False))
        self.var_color_depth.set(cfg.get("color_depth", False))
        self.var_csv.set(cfg.get("csv", True))
        self.var_c2pa.set(cfg.get("c2pa", False))

    def save_checkbox_state(self):
        cfg = {
            "show_filename": self.var_show_filename.get(),
            "gps": self.var_gps.get(),
            "location": self.var_location.get(),
            "altitude": self.var_altitude.get(),
            "date": self.var_date.get(),
            "time": self.var_time.get(),
            "camera": self.var_camera.get(),
            "focal": self.var_focal.get(),
            "focal35": self.var_focal35.get(),
            "iso": self.var_iso.get(),
            "shutter": self.var_shutter.get(),
            "aperture": self.var_aperture.get(),
            "lens": self.var_lens.get(),
            "size": self.var_size.get(),
            "color_depth": self.var_color_depth.get(),
            "csv": self.var_csv.get(),
            "c2pa": self.var_c2pa.get(),
        }
        save_config(cfg)

    def on_send_to_perplexity(self):
        #try:
        #    prompt = getattr(self, "perplexity_prompt", None)
        #    if not prompt:
        #        messagebox.showwarning("Perplexity", "No photo metadata available yet.")
        #        return
        #    send_prompt_to_perplexity(prompt)
        #except Exception as e:
        #    messagebox.showerror("Perplexity error", str(e))
        prompt = getattr(self, "perplexity_prompt", "")
        if not prompt:
            return  # nothing to send yet

        # 1) Replace the EXIF extract with the prompt text
        self.clear_info()
        self.set_info(prompt)
        #self.info_text.insert("1.0", prompt)

        # 2) Copy the prompt to the clipboard
        self.clipboard_clear()
        self.clipboard_append(prompt)
        self.update()  # keep clipboard after window loses focus
        messagebox.showinfo("Success", "Prompt copied to clipboard.")

    # ----- handlers -----

    def browse_file(self):
        filename = filedialog.askopenfilename(
            title="Select a photo",
            filetypes=[("Image files", "*.jpg;*.jpeg;*.JPG;*.JPEG")]
        )
        if filename:
            self.file_path.set(filename)
            self.show_preview(filename)

    def _apply_orientation_for_preview(self, img):
        try:
            exif_raw = img._getexif() or {}
        except Exception:
            return img
        exif = {}
        for tag_id, value in exif_raw.items():
            tag = TAGS.get(tag_id, tag_id)
            exif[tag] = value
        orientation = exif.get("Orientation")
        if orientation == 3:
            img = img.rotate(180, expand=True)
        elif orientation == 6:
            img = img.rotate(270, expand=True)
        elif orientation == 8:
            img = img.rotate(90, expand=True)
        return img

    def show_preview(self, path):
        try:
            img = Image.open(path)
            img = self._apply_orientation_for_preview(img)
            img.thumbnail((300, 300))
            self.preview_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self.preview_label.configure(image=self.preview_img, text="")
            img.close()
            self.update_idletasks()
        except Exception:
            self.preview_label.configure(image=None, text="")
            self.preview_img = None

    def run_extraction(self):
        path = self.file_path.get()
        if not path:
            messagebox.showerror("Error", "Please choose a photo first.")
            return
        if not os.path.isfile(path):
            messagebox.showerror("Error", "File not found.")
            return
        if not any([
            self.var_gps.get(), self.var_location.get(), self.var_altitude.get(),
            self.var_date.get(), self.var_time.get(),
            self.var_camera.get(),
            self.var_focal.get(), self.var_focal35.get(),
            self.var_iso.get(), self.var_shutter.get(),
            self.var_aperture.get(), self.var_lens.get(),
            self.var_size.get(), self.var_color_depth.get(),
            self.var_c2pa.get(),
        ]):
            messagebox.showerror("Error", "Please select at least one field to extract.")
            return

        self.status_label.configure(text="Working...")
        self.clear_info()
        self.update_idletasks()

        thread = threading.Thread(target=self.process_file, args=(path,))
        thread.daemon = True
        thread.start()

    def process_file(self, path):
        try:
            img, exif = get_exif_dict(path)

            lat = lon = location_name = None
            altitude = None
            date_str = time_str = None
            camera = None
            focal_mm = focal_35mm = None
            iso = shutter = aperture = None
            lens = None
            width = height = None
            color_depth = None
            c2pa_status = None

            gps = {}
            if self.var_gps.get() or self.var_location.get() or self.var_altitude.get():
                lat, lon, gps = get_gps(exif)
            if self.var_altitude.get():
                altitude = get_altitude(gps)
            if self.var_location.get():
                location_name = reverse_geocode(lat, lon)
            if self.var_date.get() or self.var_time.get():
                date_str, time_str = get_datetime(exif)
            if self.var_camera.get():
                camera = get_camera_model(exif)
            if self.var_focal.get() or self.var_focal35.get():
                focal_mm, focal_35mm = get_focal_lengths(exif)
            if self.var_iso.get() or self.var_shutter.get() or self.var_aperture.get():
                iso, shutter, aperture = get_exposure_settings(exif)
            if self.var_lens.get():
                lens = get_lens_model(exif)
            if self.var_size.get():
                width, height = img.size
            if self.var_color_depth.get():
                color_depth = get_color_depth(img, exif)
            if self.var_c2pa.get():
                c2pa_status = check_c2pa_status(path)

            img.close()

            lines = []

            # Column 1: Image properties -> GPS
            if self.var_show_filename.get():
                lines.append(f"File: {os.path.basename(path)}")
            if self.var_size.get():
                lines.append(f"Image size: {width} x {height}")
            if self.var_color_depth.get():
                lines.append(f"Color depth (bits per channel): {color_depth}")
            if (self.var_show_filename.get() or self.var_size.get() or self.var_color_depth.get()) and lines:
                lines.append("")

            if self.var_gps.get():
                lines.append(f"Latitude: {lat}")
                lines.append(f"Longitude: {lon}")
            if self.var_altitude.get():
                lines.append(f"Altitude (m): {altitude}")
            if self.var_location.get():
                lines.append(f"Location: {location_name}")
            if (self.var_gps.get() or self.var_altitude.get() or self.var_location.get()) and lines:
                lines.append("")

            # Column 2: Image settings -> Camera
            if self.var_iso.get():
                lines.append(f"ISO: {iso}")
            if self.var_shutter.get():
                lines.append(f"Shutter speed: {shutter}")
            if self.var_aperture.get():
                lines.append(f"Aperture: {aperture}")
            if (self.var_iso.get() or self.var_shutter.get() or self.var_aperture.get()) and lines:
                lines.append("")

            if self.var_camera.get():
                lines.append(f"Camera: {camera}")
            if self.var_lens.get():
                lines.append(f"Lens: {lens}")
            if self.var_focal.get():
                lines.append(f"Focal length (mm): {focal_mm}")
            if self.var_focal35.get():
                lines.append(f"Focal length (35mm equiv): {focal_35mm}")
            if (self.var_camera.get() or self.var_lens.get() or self.var_focal.get() or self.var_focal35.get()) and lines:
                lines.append("")

            # Column 3: Date & time -> C2PA -> CSV info
            if self.var_date.get():
                lines.append(f"Date: {date_str}")
            if self.var_time.get():
                lines.append(f"Time: {time_str}")
                lines.append("")
            if self.var_c2pa.get() and c2pa_status is not None:
                lines.append(c2pa_status)

            # Prompt for Perplexity
            results_text = "\n".join(lines)
            
            #flat_metadata = results_text.replace("\n", " | ")
            flat_metadata = re.sub(r'\n+', ' | ', results_text)
            quoted_metadata = f"\"{flat_metadata}\""   # or '\"' if you prefer

            self.perplexity_prompt = (
                "You are an expert social media content creator. You'll craft concise, educational, and engaging Instagram post for me, focusing on my photo's location, animal, or object, in a semi-casual, conversational tone. "
                "You'll always start the post with “landmark/location/object/subject, city name with country emoji flag (dd/mmm/yy)” as the first line (extracting date and location from metadata). Jump an extra row between 1st and 2nd line of text. "
                "The post will be under 200 words, with 20–25 relevant hashtags, and optimized for web search visibility. #25mmLens, #photometadata and the word 'equivalent' on the 35mm equivalent hashtag are also not needed, so also remember that for next posts. Research hashtags that would increase the possibility of a post being featured by the phone/camera manufacturer. Always double-check location on the map from EXIF GPS coordinates to make sure you're identifying the place correctly. "
                "Add a row at the end (after post text and before hashtags) with information about camera used and zoom level (both number - 1x, 2x, etc. - and 35mm equivalent focal length). "
                "Never use em dashes. Use parentheses instead, if necessary. "
                f"The metadata extracted from the photo is: {quoted_metadata} . "
            )

            csv_path = None
            if self.var_csv.get():
                folder = os.path.dirname(path)
                csv_path = os.path.join(folder, "photo_exif_with_location.csv")
                file_exists = os.path.isfile(csv_path)

                fieldnames = ["filename"]
                if self.var_size.get():
                    fieldnames.extend(["width", "height"])
                if self.var_color_depth.get():
                    fieldnames.append("color_depth_bits")
                if self.var_gps.get():
                    fieldnames.extend(["latitude", "longitude"])
                if self.var_altitude.get():
                    fieldnames.append("altitude_m")
                if self.var_location.get():
                    fieldnames.append("location")
                if self.var_iso.get():
                    fieldnames.append("iso")
                if self.var_shutter.get():
                    fieldnames.append("shutter_speed")
                if self.var_aperture.get():
                    fieldnames.append("aperture")
                if self.var_camera.get():
                    fieldnames.append("camera_model")
                if self.var_lens.get():
                    fieldnames.append("lens_model")
                if self.var_focal.get():
                    fieldnames.append("focal_length_mm")
                if self.var_focal35.get():
                    fieldnames.append("focal_length_35mm")
                if self.var_date.get():
                    fieldnames.append("date")
                if self.var_time.get():
                    fieldnames.append("time")
                if self.var_c2pa.get():
                    fieldnames.append("c2pa_status")

                row = {"filename": os.path.basename(path)}
                if self.var_size.get():
                    row["width"] = width
                    row["height"] = height
                if self.var_color_depth.get():
                    row["color_depth_bits"] = color_depth
                if self.var_gps.get():
                    row["latitude"] = lat
                    row["longitude"] = lon
                if self.var_altitude.get():
                    row["altitude_m"] = altitude
                if self.var_location.get():
                    row["location"] = location_name
                if self.var_iso.get():
                    row["iso"] = iso
                if self.var_shutter.get():
                    row["shutter_speed"] = shutter
                if self.var_aperture.get():
                    row["aperture"] = aperture
                if self.var_camera.get():
                    row["camera_model"] = camera
                if self.var_lens.get():
                    row["lens_model"] = lens
                if self.var_focal.get():
                    row["focal_length_mm"] = focal_mm
                if self.var_focal35.get():
                    row["focal_length_35mm"] = focal_35mm
                if self.var_date.get():
                    row["date"] = date_str
                if self.var_time.get():
                    row["time"] = time_str
                if self.var_c2pa.get():
                    row["c2pa_status"] = c2pa_status

                write_header = not file_exists
                with open(csv_path, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    if write_header:
                        writer.writeheader()
                    writer.writerow(row)

                lines.append("")
                lines.append(f"CSV saved to: {csv_path}")

            self.set_info("\n".join(lines))
            self.status_label.configure(text="Done")
            self.prompt_btn.configure(state="normal")
            if self.var_csv.get():
                messagebox.showinfo("Success", "EXIF data extracted and saved.")
            else:
                messagebox.showinfo("Success", "EXIF data extracted (no CSV written).")
        except Exception as e:
            self.status_label.configure(text="Error")
            self.set_info(f"Error: {e}")
            messagebox.showerror("Error", f"Failed to process image:\n{e}")

    def clear_info(self):
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.configure(state="disabled")

    def set_info(self, text):
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("end", text)
        self.info_text.configure(state="disabled")

def main():
    app = ExifGUI()
    app.mainloop()

if __name__ == "__main__":

    main()
