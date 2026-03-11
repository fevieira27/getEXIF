getEXIF - Image Metadata Inspector
This Python app is a desktop GUI tool for inspecting privacy‑relevant metadata in image files. It’s built with CustomTkinter and focuses on both classic EXIF data and modern C2PA “Content Credentials” metadata.

Features

Batch EXIF extraction
Load an image and view its camera model, focal length, capture date and time, GPS coordinates, and other standard EXIF fields.
Results are shown in a clean, human‑readable layout and can be exported to CSV, one row per image.

C2PA / Content Credentials inspection
Uses numbers-c2pa and c2patool under the hood to detect and parse C2PA manifests when present.
Displays validation state (e.g. “Valid”), signer info (issuer / common name), and CAWG identity assertions when available (e.g. verified name, social accounts).
​Reads CAWG / C2PA training‑and‑data‑mining assertions and surfaces AI usage permissions such as “AI inference: notAllowed” and “AI training: notAllowed.”
​
Privacy‑aware summary view
Combines EXIF and C2PA into a compact summary block per image (camera, date/time, GPS, C2PA validity, owner identity, AI‑training flags).
Helps quickly spot high‑risk fields such as precise GPS coordinates, creation times, and AI training permissions that might impact privacy or licensing.

CSV export with flat columns
Exports key fields to CSV for analysis: camera info, focal length, date/time, GPS, C2PA state, owner, AI inference/training flags, etc.
Designed so every C2PA attribute (state, owner, AI usage) has its own flat column for easy filtering and pivoting in spreadsheets.

AI‑ready prompt builder
Generates a single, cleaned AI prompt combining all relevant metadata; wraps the metadata in quotes and normalizes newlines to | separators for safe pasting into chat UIs.
On button click, the app replaces the metadata view with this prompt and copies it to the system clipboard so you can paste it directly into Perplexity, Claude, or any other AI assistant.

Tech stack - Python, with:
CustomTkinter for a modern, themed GUI.
Standard EXIF libraries (e.g. Pillow, exif or similar) to read image metadata.
numbers‑c2pa + c2patool for reading and validating C2PA manifests including CAWG identity and training‑and‑data‑mining assertions.

Typical workflow
Select one or more image files in the GUI.
Click Run to extract EXIF and C2PA metadata, view a human‑readable summary, and optionally save results as CSV.
If you want an AI opinion on the photo, click "Create AI Prompt" to transform the summary into a ready‑made prompt and copy it to your clipboard, then paste it into your AI chat of choice.
