from pathlib import Path
import runpy

# The former AI demo page is now the real pharmacy stock workflow.
# Keep this filename so existing Streamlit links/menu entries continue to work.
runpy.run_path(
    str(Path(__file__).with_name("4_💊_Pharmacy_CSA.py")),
    run_name="__main__",
)
