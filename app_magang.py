import io
import re
import string
import zipfile
from datetime import datetime

import pandas as pd
import pdfplumber
import streamlit as st
from PIL import Image
import plotly.express as px
import uuid

# ==============================================================================
# SETUP OCR (TESSERACT) - Disimpan buat jaga-jaga
# ==============================================================================
try:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
except ImportError:
    pass

# ==============================================================================
# CONFIG & SETUP
# ==============================================================================
st.set_page_config(page_title="PT Mitranet Dashboard", page_icon="🪢", layout="wide")

if "file_uploader_key" not in st.session_state:
    st.session_state["file_uploader_key"] = 0

# ==============================================================================
# KONSTANTA & POLA REGEX
# ==============================================================================
PREFIX_PROVIDER = (
    r'^(Bank Mandiri|Bank Permata|Bank Danamon|Bank Jateng|Bank BRI|'
    r'BRIVA|BNI|BCA VA|BCA|SMBC)\s*[-]*\s*'
)
SUFFIX_PATTERNS = [
    r'[-]*\s*SNAP\s*\(DEV\)',
    r'[-]*\s*SNAP\s*\(PROD\)',
    r'[-]*\s*DEV',
    r'[-]*\s*PROD',
]

POLA_BARIS = re.compile(r'^(\d+)\s+(.+?)\s+([\d.,]+)(?:\s+([\d.,]+)\s+([\d.,]+))?$')
POLA_BULAN = re.compile(r'(Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des)[a-z]* 20\d\d', re.IGNORECASE)

MAP_BULAN = {
    'Jan': 'Januari', 'Feb': 'Februari', 'Mar': 'Maret', 'Apr': 'April',
    'Mei': 'Mei', 'Jun': 'Juni', 'Jul': 'Juli', 'Agu': 'Agustus',
    'Sep': 'September', 'Okt': 'Oktober', 'Nov': 'November', 'Des': 'Desember',
}

URUTAN_BULAN = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni", 
    "Juli", "Agustus", "September", "Oktober", "November", "Desember"
]

MAP_BULAN_ANGKA = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
    7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember"
}

class CachedFile:
    def __init__(self, name, size, data):
        self.name = name
        self.size = size
        self.data = data
    def getvalue(self): 
        return self.data

def get_widget(menu, key, default):
    return st.session_state.state_data[menu]['widget_states'].get(key, default)

def set_widget(menu, key, val):
    st.session_state.state_data[menu]['widget_states'][key] = val

# ==============================================================================
# FUNGSI HELPER UMUM
# ==============================================================================
def kategori_bank(row):
    if isinstance(row, pd.Series) or isinstance(row, dict):
        if "Bank Utama" in row and pd.notna(row["Bank Utama"]) and str(row["Bank Utama"]).strip() != "":
            return str(row["Bank Utama"]).strip()
        teks = str(row.get("Provider_Asli", "")).upper()
    else:
        teks = str(row).upper()

    if "MANDIRI" in teks: return "VA MANDIRI"
    if "BRI" in teks: return "VA BRI"
    if "BNI" in teks: return "VA BNI"
    if "BTN" in teks: return "VA BTN"
    if "BCA" in teks: return "VA BCA"
    if "PERMATA" in teks: return "VA PERMATA"
    if "JATENG" in teks: return "VA JATENG"
    if "SMBC" in teks: return "VA SMBC"
    
    return "VA LAINNYA"

def bersihkan_nama_bpr(teks: str) -> str:
    teks = str(teks)
    teks = re.sub(PREFIX_PROVIDER, '', teks, flags=re.IGNORECASE)
    for pola in SUFFIX_PATTERNS:
        teks = re.sub(pola, '', teks, flags=re.IGNORECASE)
    return teks.strip()

def deteksi_bulan(teks_dokumen: str) -> str:
    pola_bulan = POLA_BULAN.search(teks_dokumen)
    if pola_bulan:
        bulan_singkat = pola_bulan.group(1).title()
        return MAP_BULAN.get(bulan_singkat, 'Bulan Tidak Diketahui')
    return 'Bulan Tidak Diketahui'

def aman_ke_angka(val) -> int:
    if pd.isna(val): return 0
    if isinstance(val, (int, float)): return int(val)
    teks = str(val).strip()
    if teks.endswith('.0'):
        teks = teks[:-2]
    teks = re.sub(r'[^\d]', '', teks)
    return int(teks) if teks else 0

def format_ribuan(angka) -> str:
    if pd.isna(angka) or angka == '': return ""
    try:
        return f"{int(angka):,}".replace(',', '.')
    except (ValueError, TypeError):
        return str(angka)

def parse_tanggal_pintar(series: pd.Series) -> pd.Series:
    asli = series
    if series.dtype == object:
        series = series.astype(str).str.strip().str.replace('\xa0', ' ', regex=False)
        series = series.str.replace(
            r'(\d{1,2})\.(\d{2})(\.(\d{2}))?\s*$',
            lambda m: f"{m.group(1)}:{m.group(2)}:{m.group(4)}" if m.group(4) else f"{m.group(1)}:{m.group(2)}",
            regex=True,
        )
    hasil = pd.to_datetime(series, format='mixed', errors='coerce', dayfirst=True)
    mask_gagal = hasil.isna()
    if mask_gagal.any():
        format_alternatif = [
            '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
            '%d/%m/%Y %H.%M.%S', '%d/%m/%Y %H.%M',
            '%Y-%m-%d %H:%M:%S', '%Y-%m-%d',
            '%d-%m-%Y %H:%M:%S', '%d-%m-%Y',
            '%m/%d/%Y %H:%M:%S', '%m/%d/%Y',
        ]
        for fmt in format_alternatif:
            if not mask_gagal.any(): break
            coba = pd.to_datetime(series[mask_gagal], format=fmt, errors='coerce')
            hasil.loc[mask_gagal] = coba
            mask_gagal = hasil.isna()
    if mask_gagal.any():
        angka_mentah = pd.to_numeric(asli[mask_gagal], errors='coerce')
        serial_valid = angka_mentah.notna() & (angka_mentah > 20000) & (angka_mentah < 60000)
        if serial_valid.any():
            idx_serial = angka_mentah[serial_valid].index
            hasil.loc[idx_serial] = pd.to_datetime(angka_mentah.loc[idx_serial], unit='D', origin='1899-12-30')
    return hasil

def cari_kolom_tanggal(df: pd.DataFrame):
    kandidat = ["tanggal", "tgl", "date", "tanggal transaksi", "transaction date", "posting date"]
    for col in df.columns:
        nama = str(col).lower().strip()
        if any(k in nama for k in kandidat):
            return col
    return None

def isi_bulan_otomatis(df: pd.DataFrame):
    df = df.copy()
    col_tanggal = cari_kolom_tanggal(df)
    if col_tanggal is None:
        return df, False
    tanggal = parse_tanggal_pintar(df[col_tanggal])
    if tanggal.notna().sum() == 0:
        return df, False
    df["Bulan"] = tanggal.dt.month.map(MAP_BULAN_ANGKA)
    return df, True

# ==============================================================================
# FUNGSI EKSTRAKSI DATA UTAMA
# ==============================================================================
@st.cache_data(show_spinner=False)
def ekstrak_teks_pdf(file_bytes: bytes) -> str:
    teks_full = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            teks_halaman = page.extract_text()
            if teks_halaman: teks_full += teks_halaman + "\n"
    return teks_full

def baca_csv_pintar(file):
    encodings = ['utf-8', 'utf-8-sig', 'latin1', 'cp1252']
    bytes_data = file.getvalue()
    for enc in encodings:
        try:
            teks = bytes_data.decode(enc)
            baris_list = teks.splitlines()
            if not baris_list: continue
            header_idx, separator, max_cols = 0, ',', 0
            for idx, baris in enumerate(baris_list[:20]):
                for sep in [',', ';', '\t']:
                    cols = baris.split(sep)
                    if len(cols) > max_cols:
                        teks_lower = baris.lower()
                        if any(k in teks_lower for k in ['tanggal', 'date', 'kode', 'no', 'user', 'total', 'mitra', 'nominal', 'produk']):
                            max_cols, header_idx, separator = len(cols), idx, sep
            if max_cols == 0:
                for idx, baris in enumerate(baris_list[:10]):
                    for sep in [',', ';', '\t']:
                        cols = baris.split(sep)
                        if len(cols) > max_cols and len(cols) > 1:
                            max_cols, header_idx, separator = len(cols), idx, sep
            df = pd.read_csv(io.BytesIO(bytes_data), sep=separator if max_cols > 0 else ',', skiprows=header_idx, engine='python', encoding=enc, on_bad_lines='skip')
            if not df.empty:
                df.columns = [str(c).strip() for c in df.columns]
                return df
        except: continue
    try: return pd.read_csv(io.BytesIO(bytes_data), sep=None, engine='python', on_bad_lines='skip')
    except: return pd.DataFrame()

def parse_baris_transaksi(teks_full: str, bulan_laporan: str = None) -> list:
    data = []
    for baris in teks_full.split('\n'):
        baris = baris.strip()
        match = POLA_BARIS.match(baris)
        if not match: continue
        provider = match.group(2)
        angka1, angka2, angka3 = match.group(3), match.group(4), match.group(5)
        row = {'Provider_Asli': provider, 'Jumlah VA': aman_ke_angka(angka1)}
        if angka2 and angka3:
            row['Jumlah Transaksi'] = aman_ke_angka(angka2)
            row['Nilai_Bulanan'] = aman_ke_angka(angka3)
        else:
            row['Jumlah Transaksi'] = 0; row['Nilai_Bulanan'] = 0
        if bulan_laporan: row['Bulan'] = bulan_laporan
        data.append(row)
    return data

@st.cache_data(show_spinner=False)
def ekstrak_semua_tabel_docx(file_bytes: bytes):
    try: import docx
    except ImportError: return []
    doc = docx.Document(io.BytesIO(file_bytes))
    kumpulan_tabel = []
    for i, table in enumerate(doc.tables):
        data = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        if len(data) > 1:
            cols = data[0]
            seen, new_cols = {}, []
            for c in cols:
                c = c.replace('\n', ' ').strip() or "Unnamed"
                if c in seen: seen[c] += 1; new_cols.append(f"{c}_{seen[c]}")
                else: seen[c] = 0; new_cols.append(c)
            df = pd.DataFrame(data[1:], columns=new_cols)
            kumpulan_tabel.append((f"Tabel {i+1} (Kolom: {', '.join(new_cols[:3])}...)", df))
    return kumpulan_tabel

def parse_baris_word_dinamis(teks_full: str, bulan_laporan: str) -> list:
    data, mode_rekam, temp_data = [], False, {}
    for baris in teks_full.split('\n'):
        teks = baris.strip().upper()
        if not teks: continue
        if any(k in teks for k in ["LAPORAN PAYMENT", "REKAP", "TRANSAKSI", "CREATE"]): mode_rekam = True
        if "MENGETAHUI" in teks or "PT. INDOTEK" in teks:
            if temp_data and 'Provider_Asli' in temp_data:
                if '_tunggu' in temp_data: del temp_data['_tunggu']
                if temp_data not in data: data.append(temp_data.copy())
            break
        if not mode_rekam: continue
        teks_koreksi = teks
        if sum(c.isdigit() for c in teks) > 2: 
            teks_koreksi = teks_koreksi.replace('S', '5').replace('O', '0').replace('l', '1').replace('I', '1')
        angka_saja = re.sub(r'[^\d]', '', teks_koreksi)

        if "JUMLAH TRANSAKSI" in teks:
            if temp_data:
                if angka_saja: temp_data['Jumlah Transaksi'] = int(angka_saja)
                else: temp_data['_tunggu'] = 'jumlah'
            continue
        if "NOMINAL TRANSAKSI" in teks:
            if temp_data:
                if angka_saja: temp_data['Nilai_Bulanan'] = int(angka_saja)
                else: temp_data['_tunggu'] = 'nominal'
            continue
        teks_cek = re.sub(r'[RPIDR\s\.,:-]', '', teks_koreksi)
        if angka_saja and (len(teks_cek) == len(angka_saja) or teks_cek.isdigit()):
            if temp_data and '_tunggu' in temp_data:
                if temp_data['_tunggu'] == 'jumlah': temp_data['Jumlah Transaksi'] = int(angka_saja)
                elif temp_data['_tunggu'] == 'nominal': temp_data['Nilai_Bulanan'] = int(angka_saja)
                del temp_data['_tunggu']
            continue
        if len(teks) > 3 and not angka_saja and not any(k in teks for k in ["CREATE", "TRANSAKSI"]):
            if temp_data and 'Provider_Asli' in temp_data:
                if '_tunggu' in temp_data: del temp_data['_tunggu']
                data.append(temp_data.copy())
            temp_data = {'Provider_Asli': teks.title(), 'Jumlah VA': 0, 'Jumlah Transaksi': 0, 'Nilai_Bulanan': 0, 'Bulan': bulan_laporan if bulan_laporan else "Bulan Tidak Diketahui"}
    if temp_data and 'Provider_Asli' in temp_data and temp_data not in data:
        if '_tunggu' in temp_data: del temp_data['_tunggu']
        data.append(temp_data)
    return data

# ==============================================================================
# 🎛️ SIDEBAR NAVIGASI
# ==============================================================================
if 'state_data' not in st.session_state:
    st.session_state.state_data = {
        'rekap': {'audit_log': [], 'processed_files': set(), 'file_cache': [], 'widget_states': {}, 'ocr_results': {}},
        'mbanking': {'audit_log': [], 'processed_files': set(), 'file_cache': [], 'widget_states': {}},
    }

with st.sidebar:
    st.title("Menu Navigasi")
    menu_terpilih = st.sidebar.radio( 
        "Pilih Modul Aplikasi:",
        [
            "Laporan VA (Semua Format)",
            "Laporan MBanking (Matrix 1-31)",
            "Laporan Qris",
            "Analisis Persentase"
        ]
    )

# ==============================================================================
# JALUR 1 : Laporan VA (Semua Format)
# ==============================================================================
if menu_terpilih == "Laporan VA (Semua Format)":
    nama_menu = 'rekap'
    st.title("🔄 Modul Laporan VA (Semua Format)")
    st.markdown("Upload laporan PDF/CSV/Excel/Word. Dilengkapi fitur Sheet selector & OCR lokal!")
    st.markdown("---")

    # --- 1. BIKIN KOLOMNYA DULU ---
    col_up1, col_up2 = st.columns([4, 1])

    # --- 2. ISI KOLOM KIRI (UPLOADER) ---
    with col_up1:
        uploaded_files = st.file_uploader(
            "Upload Laporan Mobile Banking", 
            accept_multiple_files=True, 
            key=f"uploader_mbanking_{st.session_state['file_uploader_key']}"
        )

    # --- 3. ISI KOLOM KANAN (TOMBOL CLEAR) ---
    with col_up2:
        st.write("") 
        st.write("")
        if st.button("🗑️ Bersihkan File", key="btn_clear_mbanking", use_container_width=True):
            st.session_state["file_uploader_key"] += 1
            # BARIS SAKTI: Hapus ingatan memori cache secara paksa
            st.session_state.state_data[nama_menu]['file_cache'] = []
            st.rerun()

    # --- 4. LANJUTAN KODINGAN ASLIMU ---
    if uploaded_files:
        st.session_state.state_data[nama_menu]['file_cache'] = [{'name': f.name, 'size': f.size, 'data': f.getvalue()} for f in uploaded_files]

    active_files = st.session_state.state_data[nama_menu]['file_cache']

    if active_files:
        semua_data_terpilih = []
        st.markdown("### 🎛️ Ekstraksi Data Vertikal & Matriks")
        
        # --- 1. TAMBAHKAN PALET WARNA DI SINI ---
        warna_palette = ['#FF4B4B', '#00b894', '#0984e3', '#fdcb6e', '#6c5ce7', '#e84393']
        idx_warna = 0
        # ----------------------------------------

        for f_dict in active_files:
            file = CachedFile(f_dict['name'], f_dict['size'], f_dict['data'])
            file_id = f"{file.name}_{file.size}"
            
            saved_aktif = get_widget(nama_menu, f"aktif_{file_id}", True)
            file_aktif = st.checkbox(f"🟢 Aktifkan File: {file.name}", value=saved_aktif, key=f"rek_aktif_{file_id}")
            set_widget(nama_menu, f"aktif_{file_id}", file_aktif)

            if not file_aktif: continue
            
            # --- 2. INJEKSI HTML WARNA DI SINI (SEBELUM TRY) ---
            warna_pilihan = warna_palette[idx_warna % len(warna_palette)]
            idx_warna += 1
            st.markdown(f'<div style="height: 10px; background-color: {warna_pilihan}; border-radius: 5px 5px 0 0; margin-bottom: -0.5rem; z-index: 99; position: relative;"></div>', unsafe_allow_html=True)
            # ---------------------------------------------------
            
            try:
                # ====== LOGIKA PDF ======
                if file.name.endswith('.pdf'):
                    teks_full = ekstrak_teks_pdf(file.getvalue())
                    
                    # --- 1. JURUS DETEKSI GANDA (Cek Isi Teks, Kalau Gagal Cek Nama File) ---
                    bulan_laporan = deteksi_bulan(teks_full)
                    if bulan_laporan == 'Bulan Tidak Diketahui':
                        bulan_laporan = deteksi_bulan(file.name)
                    # ------------------------------------------------------------------------

                    raw_data = parse_baris_transaksi(teks_full, bulan_laporan)
                    if not raw_data: continue

                    df_file = pd.DataFrame(raw_data)
                    df_file = df_file[~df_file['Provider_Asli'].astype(str).str.lower().str.contains('total', na=False)]

                    with st.expander(f"📄 Pengaturan PDF: {file.name}", expanded=True):
                        
                        # 1. Pilih Bulan
                        opsi_bulan = URUTAN_BULAN + ['Bulan Tidak Diketahui']
                        saved_bln = get_widget(nama_menu, f"bln_pdf_{file_id}", bulan_laporan)
                        if saved_bln not in opsi_bulan: 
                            saved_bln = bulan_laporan if bulan_laporan in opsi_bulan else 'Bulan Tidak Diketahui'
                            
                        pilih_bulan = st.selectbox("📅 Konfirmasi / Revisi Bulan:", options=opsi_bulan, index=opsi_bulan.index(saved_bln), key=f"rek_bln_pdf_{file_id}")
                        set_widget(nama_menu, f"bln_pdf_{file_id}", pilih_bulan)
                        df_file["Bulan"] = pilih_bulan

                        # 2. PINDAH KE ATAS: Multiselect buat centang metrik
                        def_cols = ['Jumlah VA', 'Jumlah Transaksi', 'Nominal Masuk']
                        saved_cols = get_widget(nama_menu, f"cols_{file_id}", def_cols)
                        valid_cols = [c for c in saved_cols if c in def_cols]

                        kolom_pilihan = st.multiselect("Centang angka yang ingin di-Pivot:", options=def_cols, default=valid_cols, key=f"rek_cols_{file_id}")
                        set_widget(nama_menu, f"cols_{file_id}", kolom_pilihan)

                        # 3. LOGIKA BARU: Tentukan kolom apa aja yang mau ditampilin di Preview
                        kolom_tampil = ['Provider_Asli']
                        if 'Jumlah VA' in kolom_pilihan: kolom_tampil.append('Jumlah VA')
                        if 'Jumlah Transaksi' in kolom_pilihan: kolom_tampil.append('Jumlah Transaksi')
                        if 'Nominal Masuk' in kolom_pilihan: kolom_tampil.append('Nilai_Bulanan')
                        kolom_tampil.append('Bulan')

                        # 4. Gambar Tabel Preview (Cuma nampilin kolom yang ada di list kolom_tampil)
                        st.dataframe(df_file[kolom_tampil], use_container_width=True)

                        # 5. Persiapan data untuk dilempar ke Tabel Master Gabungan
                        df_file_filtered = df_file.copy()
                        if 'Jumlah VA' not in kolom_pilihan: df_file_filtered['Jumlah VA'] = 0
                        if 'Jumlah Transaksi' not in kolom_pilihan: df_file_filtered['Jumlah Transaksi'] = 0
                        if 'Nominal Masuk' not in kolom_pilihan: df_file_filtered['Nilai_Bulanan'] = 0

                        df_file_filtered["Bulan"] = df_file_filtered["Bulan"].astype(str).str.capitalize()
                        df_file_filtered["Bulan"] = pd.Categorical(df_file_filtered["Bulan"], categories=URUTAN_BULAN, ordered=True)
                        
                        df_file_filtered['Bank Utama'] = df_file_filtered.apply(kategori_bank, axis=1)
                        df_file_filtered['Nama BPR Bersih'] = df_file_filtered['Provider_Asli'].apply(bersihkan_nama_bpr)
                        semua_data_terpilih.append(df_file_filtered)

                # ====== LOGIKA WORD (.DOCX) ======
                elif file.name.endswith('.docx'):
                    kumpulan_tabel_matriks = ekstrak_semua_tabel_docx(file.getvalue())
                    
                    if not kumpulan_tabel_matriks:
                        st.warning(f"⚠️ Tabel asli tidak ditemukan. Beralih ke Tesseract OCR (Image Enhancer) 🤖")
                        
                        ocr_cache_key = f"ocr_mentah_{file_id}"
                        saved_ocr_text = get_widget(nama_menu, ocr_cache_key, "Isi manual teks OCR di sini jika gambar tidak ada")
                        
                        with st.expander(f"👁️ Review & Koreksi AI OCR: {file.name}", expanded=True):
                            edited_ocr_text = st.text_area("Teks Mentah Hasil Ekstraksi (Silakan Edit):", value=saved_ocr_text, height=300, key=f"edit_{file_id}")
                            if st.button("🚀 Proses Data OCR ke Tabel Master", key=f"btn_ocr_{file_id}"):
                                raw_data = parse_baris_word_dinamis(edited_ocr_text, "Januari")
                                if raw_data:
                                    df_ocr = pd.DataFrame(raw_data)
                                    df_ocr = df_ocr[~df_ocr['Provider_Asli'].astype(str).str.lower().str.contains('total', na=False)]
                                    df_ocr['Bank Utama'] = df_ocr.apply(kategori_bank, axis=1)
                                    df_ocr['Nama BPR Bersih'] = df_ocr['Provider_Asli'].apply(bersihkan_nama_bpr)
                                    st.session_state.state_data[nama_menu]['ocr_results'][file_id] = df_ocr
                                    st.success(f"✅ AI Berhasil mengekstrak {len(df_ocr)} baris data!")
                                    st.rerun()

                        if file_id in st.session_state.state_data[nama_menu]['ocr_results']:
                            df_ocr_final = st.session_state.state_data[nama_menu]['ocr_results'][file_id]
                            st.dataframe(df_ocr_final, use_container_width=True)
                            semua_data_terpilih.append(df_ocr_final)
                        continue

                # ====== LOGIKA EXCEL & CSV ======
                elif file.name.endswith(('.csv', '.xlsx', '.xls')):
                    data_sheet_list = []
                    
                    if file.name.endswith('.csv'):
                        df_raw = baca_csv_pintar(file)
                        if not df_raw.empty: data_sheet_list.append((file.name, df_raw))
                    else:
                        xls_file = pd.ExcelFile(io.BytesIO(file.getvalue()))
                        nama_nama_sheet = xls_file.sheet_names
                        saved_sheets = get_widget(nama_menu, f"sheets_{file_id}", nama_nama_sheet)
                        valid_sheets = [s for s in saved_sheets if s in nama_nama_sheet]
                        with st.expander(f"📑 Pilih Sheet dari: {file.name}", expanded=True):
                            pilih_sheet = st.multiselect("Centang Sheet yang ingin diproses:", options=nama_nama_sheet, default=valid_sheets, key=f"rek_sheets_{file_id}")
                            set_widget(nama_menu, f"sheets_{file_id}", pilih_sheet)
                        for sht in pilih_sheet:
                            df_sht = pd.read_excel(xls_file, sheet_name=sht)
                            if not df_sht.empty: data_sheet_list.append((f"{file.name} - [{sht}]", df_sht))

                    for nama_sumber, df_raw in data_sheet_list:
                        sumber_id = f"{file_id}_{nama_sumber}"
                        
                        # --- 🚀 JURUS SUPER PAGAR BANK & AUTO-HEADER ---
                        bulan_list = [b.lower() for b in MAP_BULAN.values()]
                        is_matrix = False
                        
                        # Cek apakah ini file Matriks (bulan menyamping)
                        if any(b in [str(c).lower().strip() for c in df_raw.columns] for b in bulan_list):
                            is_matrix = True
                        else:
                            for i in range(min(6, len(df_raw))):
                                if any(b in [str(x).lower().strip() for x in df_raw.iloc[i].values] for b in bulan_list):
                                    is_matrix = True
                                    break
                                    
                        if is_matrix:
                            # 1. Turunkan header asli jadi baris data
                            temp_df = df_raw.copy()
                            temp_df.loc[-1] = temp_df.columns
                            temp_df.index = temp_df.index + 1
                            temp_df = temp_df.sort_index()
                            temp_df.columns = [f"Col_{i}" for i in range(temp_df.shape[1])]
                            
                            # 2. Bikin Pagar Bank Utama Otomatis dari Kolom 0 (Kolom A di Excel)
                            current_bank = pd.NA
                            bank_list_temp = []
                            for val in temp_df["Col_0"]:
                                val_str = str(val).strip().upper()
                                # Tangkap kata kunci "VA BNI", "VA BRI", dll di Kolom A
                                if val_str.startswith("VA ") and len(val_str) <= 15:
                                    current_bank = val_str
                                bank_list_temp.append(current_bank)
                                
                            temp_df["Bank Utama Pagar"] = bank_list_temp
                            
                            # 3. Auto-Header (Naikin baris Januari, Februari jadi Judul Kolom)
                            for i in range(min(7, len(temp_df))):
                                baris_isi = [str(x).lower().strip() for x in temp_df.iloc[i].values]
                                if any(b in baris_isi for b in bulan_list):
                                    new_header = []
                                    for idx_col, val in enumerate(temp_df.iloc[i].values):
                                        val_str = str(val).strip()
                                        if temp_df.columns[idx_col] == "Bank Utama Pagar":
                                            new_header.append("Bank Utama") # Header kolom bank kita
                                        elif pd.isna(val) or val_str.lower() in ['none', 'nan', '']:
                                            new_header.append(f"KolomKosong_{idx_col}") # Anti duplikat Unnamed
                                        else:
                                            new_header.append(val_str)
                                            
                                    # Pastikan 1000% gak ada nama kembar
                                    seen = {}
                                    final_header = []
                                    for c in new_header:
                                        if c in seen:
                                            seen[c] += 1
                                            final_header.append(f"{c}_{seen[c]}")
                                        else:
                                            seen[c] = 0
                                            final_header.append(c)
                                            
                                    temp_df.columns = final_header
                                    df_raw = temp_df.iloc[i+1:].reset_index(drop=True)
                                    break
                        # ----------------------------------------------------------

                        if is_matrix:
                            df_raw.columns = [str(c).strip() for c in df_raw.columns]
                            
                            with st.expander(f"📝 Bedah Matriks: {nama_sumber}", expanded=True):
                                # --- 1. MUNCULIN PREVIEW TABEL LAGI ---
                                st.dataframe(df_raw, use_container_width=True)
                                # --------------------------------------
                                
                                col1, col2 = st.columns(2)
                                
                                opsi_prov = [c for c in df_raw.columns if any(x in c.lower() for x in ['provider', 'bpr', 'nama', 'mitra'])]
                                def_prov = opsi_prov[0] if opsi_prov else df_raw.columns[1]
                                
                                nama_file_lower = nama_sumber.lower()
                                if "nom" in nama_file_lower: default_jenis = 2
                                elif "transaksi" in nama_file_lower or "trx" in nama_file_lower or "jum" in nama_file_lower: default_jenis = 1
                                else: default_jenis = 0
                                
                                with col1:
                                    col_prov = st.selectbox("📌 Kolom Provider/BPR:", options=df_raw.columns, index=list(df_raw.columns).index(def_prov), key=f"p_prov_{sumber_id}")
                                with col2:
                                    jenis_data = st.selectbox("Tabel ini berisi:", ["Jumlah VA", "Jumlah Transaksi", "Nominal Masuk"], index=default_jenis, key=f"p_jenis_{sumber_id}")
                                
                                cols_bulan_terdeteksi = [col for col in df_raw.columns if str(col).strip().lower() in [b.lower() for b in URUTAN_BULAN]]
                                
                                if not cols_bulan_terdeteksi:
                                    st.error("❌ Gak nemu kolom bulan (Jan-Des)! Cek lagi nama header Excel-mu.")
                                else:
                                    col_bank_utama = next((c for c in df_raw.columns if "bank" in str(c).lower() and "utama" in str(c).lower()), None)
                                    id_vars_list = [col_prov]
                                    
                                    if col_bank_utama and col_bank_utama != col_prov:
                                        df_raw[col_bank_utama] = df_raw[col_bank_utama].replace(r'^\s*$', pd.NA, regex=True).ffill()
                                        id_vars_list.append(col_bank_utama)

                                    df_melt = df_raw.melt(id_vars=id_vars_list, value_vars=cols_bulan_terdeteksi, var_name='Bulan', value_name='Nilai')
                                    df_melt["Bulan"] = df_melt["Bulan"].astype(str).str.strip().str.capitalize()
                                    df_melt["Bulan"] = pd.Categorical(df_melt["Bulan"], categories=URUTAN_BULAN, ordered=True)
                                    df_melt = df_melt.sort_values(["Bulan", col_prov])
                                    
                                    # --- 2. JURUS SAPU JAGAT ULTIMATE (HAPUS BARIS SAMPAH) ---
                                    # Buang kata "Total"
                                    df_melt = df_melt[~df_melt[col_prov].astype(str).str.lower().str.contains('total', na=False)]
                                    
                                    # EKSEKUSI IDE BOSKU: Hapus baris kalau nama BPR-nya kosong, 'none', 'nan', atau sisa header
                                    kata_sampah = ['no', 'nama bpr', 'nan', 'none', 'provider', '', 'null']
                                    df_melt = df_melt[~df_melt[col_prov].astype(str).str.lower().str.strip().isin(kata_sampah)]
                                    
                                    # Tambahan: Hapus baris yang cell BPR-nya benar-benar kosong (NaN/NaT di Pandas)
                                    df_melt = df_melt.dropna(subset=[col_prov])
                                    
                                    # Buang kalau isinya cuma nama grup bank (misal: "VA BNI") di kolom BPR
                                    df_melt = df_melt[~df_melt[col_prov].astype(str).str.upper().str.startswith('VA ')]
                                    # --------------------------------------------------------
                                    
                                    df_melt['Nilai'] = df_melt['Nilai'].apply(aman_ke_angka)
                                    
                                    df_ext = pd.DataFrame()
                                    df_ext['Provider_Asli'] = df_melt[col_prov]
                                    df_ext['Bulan'] = df_melt['Bulan']
                                    if col_bank_utama:
                                        df_ext['Bank Utama'] = df_melt[col_bank_utama]
                                        
                                    df_ext['Jumlah VA'] = df_melt['Nilai'] if jenis_data == "Jumlah VA" else 0
                                    df_ext['Jumlah Transaksi'] = df_melt['Nilai'] if jenis_data == "Jumlah Transaksi" else 0
                                    df_ext['Nilai_Bulanan'] = df_melt['Nilai'] if jenis_data == "Nominal Masuk" else 0
                                    
                                    df_ext['Bank Utama'] = df_ext.apply(kategori_bank, axis=1)
                                    df_ext['Nama BPR Bersih'] = df_ext['Provider_Asli'].apply(bersihkan_nama_bpr)
                                    
                                    semua_data_terpilih.append(df_ext)
                                    st.success(f"✅ {len(cols_bulan_terdeteksi)} Bulan (Otomatis masuk Tabel Master)")

                        else:
                            with st.expander(f"📄 Pengaturan Vertikal: {nama_sumber}", expanded=True):
                                st.dataframe(df_raw, use_container_width=True)
                                opsi_kolom = ["-- Abaikan / Tidak Ada --"] + list(df_raw.columns)
                                df_to_process = df_raw.copy()
                                
                                col_bank_utama = next((c for c in df_to_process.columns if "bank" in str(c).lower() and "utama" in str(c).lower()), None)
                                if col_bank_utama:
                                    df_to_process[col_bank_utama] = df_to_process[col_bank_utama].replace(r'^\s*$', pd.NA, regex=True).ffill()

                                df_to_process, bulan_otomatis = isi_bulan_otomatis(df_to_process)
                                if bulan_otomatis:
                                    st.success("✅ Bulan berhasil dideteksi otomatis dari kolom tanggal.")
                                else:
                                    saved_bln = get_widget(nama_menu, f"bln_{sumber_id}", "Januari")
                                    input_bulan = st.selectbox("📅 Bulan laporan", URUTAN_BULAN, index=URUTAN_BULAN.index(saved_bln) if saved_bln in URUTAN_BULAN else 0, key=f"rek_bln_{sumber_id}")
                                    set_widget(nama_menu, f"bln_{sumber_id}", input_bulan)
                                    df_to_process["Bulan"] = input_bulan

                                st.markdown("**⚙️ Pilih Data yang Tersedia di Laporan:**")
                                col_chk1, col_chk2, col_chk3, col_chk4 = st.columns(4)
                                
                                saved_chk_va = get_widget(nama_menu, f"chk_va_{sumber_id}", True)
                                use_va = col_chk1.checkbox("Ada Jumlah VA?", value=saved_chk_va, key=f"rek_chk_va_{sumber_id}")
                                set_widget(nama_menu, f"chk_va_{sumber_id}", use_va)
                                
                                saved_chk_trx = get_widget(nama_menu, f"chk_trx_{sumber_id}", True)
                                use_trx = col_chk2.checkbox("Ada Jml Transaksi?", value=saved_chk_trx, key=f"rek_chk_trx_{sumber_id}")
                                set_widget(nama_menu, f"chk_trx_{sumber_id}", use_trx)
                                
                                saved_chk_nom = get_widget(nama_menu, f"chk_nom_{sumber_id}", True)
                                use_nom = col_chk3.checkbox("Ada Nominal?", value=saved_chk_nom, key=f"rek_chk_nom_{sumber_id}")
                                set_widget(nama_menu, f"chk_nom_{sumber_id}", use_nom)
                                
                                saved_chk_stat = get_widget(nama_menu, f"chk_stat_{sumber_id}", False)
                                use_stat = col_chk4.checkbox("Ada Status?", value=saved_chk_stat, key=f"rek_chk_stat_{sumber_id}")
                                set_widget(nama_menu, f"chk_stat_{sumber_id}", use_stat)
                                st.markdown("---")

                                col1, col2, col3 = st.columns(3)
                                with col1:
                                    saved_prov = get_widget(nama_menu, f"prov_{sumber_id}", opsi_kolom[0])
                                    map_provider = st.selectbox("📌 Kolom Provider/Mitra", options=opsi_kolom, index=opsi_kolom.index(saved_prov) if saved_prov in opsi_kolom else 0, key=f"rek_prov_{sumber_id}")
                                    set_widget(nama_menu, f"prov_{sumber_id}", map_provider)

                                    if use_va:
                                        saved_va = get_widget(nama_menu, f"va_{sumber_id}", opsi_kolom[0])
                                        map_va = st.selectbox("🔢 Kolom Jumlah VA", options=opsi_kolom, index=opsi_kolom.index(saved_va) if saved_va in opsi_kolom else 0, key=f"rek_va_{sumber_id}")
                                        set_widget(nama_menu, f"va_{sumber_id}", map_va)
                                    else:
                                        map_va = "-- Abaikan / Tidak Ada --"
                                        
                                with col2:
                                    if use_trx:
                                        saved_trx = get_widget(nama_menu, f"trx_{sumber_id}", opsi_kolom[0])
                                        map_trx = st.selectbox("🔢 Kolom Jml Transaksi", options=opsi_kolom, index=opsi_kolom.index(saved_trx) if saved_trx in opsi_kolom else 0, key=f"rek_trx_{sumber_id}")
                                        set_widget(nama_menu, f"trx_{sumber_id}", map_trx)
                                    else:
                                        map_trx = "-- Abaikan / Tidak Ada --"

                                    if use_nom:
                                        saved_nom = get_widget(nama_menu, f"nom_{sumber_id}", opsi_kolom[0])
                                        map_nom = st.selectbox("💰 Kolom Nominal", options=opsi_kolom, index=opsi_kolom.index(saved_nom) if saved_nom in opsi_kolom else 0, key=f"rek_nom_{sumber_id}")
                                        set_widget(nama_menu, f"nom_{sumber_id}", map_nom)
                                    else:
                                        map_nom = "-- Abaikan / Tidak Ada --"
                                        
                                with col3:
                                    if use_stat:
                                        saved_stat = get_widget(nama_menu, f"stat_{sumber_id}", opsi_kolom[0])
                                        map_status = st.selectbox("🚦 Kolom Status (Opsional)", options=opsi_kolom, index=opsi_kolom.index(saved_stat) if saved_stat in opsi_kolom else 0, key=f"rek_stat_{sumber_id}")
                                        set_widget(nama_menu, f"stat_{sumber_id}", map_status)
                                    else:
                                        map_status = "-- Abaikan / Tidak Ada --"

                                if map_status != "-- Abaikan / Tidak Ada --":
                                    unique_status = df_to_process[map_status].astype(str).unique().tolist()
                                    saved_fil = get_widget(nama_menu, f"fil_{sumber_id}", unique_status)
                                    valid_saved_fil = [s for s in saved_fil if s in unique_status]
                                    selected_statuses = st.multiselect("✔️ Pilih Status yang Dihitung:", options=unique_status, default=valid_saved_fil, key=f"rek_fil_{sumber_id}")
                                    set_widget(nama_menu, f"fil_{sumber_id}", selected_statuses)
                                    df_to_process = df_to_process[df_to_process[map_status].astype(str).isin(selected_statuses)]

                                df_file_filtered = pd.DataFrame(index=df_to_process.index)
                                df_file_filtered["Bulan"] = df_to_process["Bulan"]
                                
                                if col_bank_utama:
                                    df_file_filtered['Bank Utama'] = df_to_process[col_bank_utama]

                                if map_provider != "-- Abaikan / Tidak Ada --": 
                                    df_to_process[map_provider] = df_to_process[map_provider].fillna("Unknown").astype(str)
                                    df_file_filtered['Provider_Asli'] = df_to_process[map_provider]
                                else: 
                                    df_file_filtered['Provider_Asli'] = "Unknown Provider"

                                df_file_filtered = df_file_filtered[df_file_filtered["Bulan"].notna()]
                                
                                list_bpr_vertikal = sorted(df_file_filtered['Provider_Asli'].astype(str).unique().tolist())
                                saved_bpr_vert = get_widget(nama_menu, f"bpr_vert_{sumber_id}", list_bpr_vertikal)
                                valid_bpr_vert = [b for b in saved_bpr_vert if b in list_bpr_vertikal]
                                
                                bpr_vert_terpilih = st.multiselect("🔍 Pilih BPR yang mau dihitung:", options=list_bpr_vertikal, default=valid_bpr_vert, key=f"rek_bpr_vert_{sumber_id}")
                                set_widget(nama_menu, f"bpr_vert_{sumber_id}", bpr_vert_terpilih)
                                
                                mask_bpr = df_file_filtered['Provider_Asli'].astype(str).isin(bpr_vert_terpilih)
                                df_file_filtered = df_file_filtered[mask_bpr]
                                df_to_process = df_to_process[mask_bpr]

                                if map_va != "-- Abaikan / Tidak Ada --": df_file_filtered['Jumlah VA'] = df_to_process[map_va].apply(aman_ke_angka)
                                else: df_file_filtered['Jumlah VA'] = 0

                                if map_trx != "-- Abaikan / Tidak Ada --": df_file_filtered['Jumlah Transaksi'] = df_to_process[map_trx].apply(aman_ke_angka)
                                else: df_file_filtered['Jumlah Transaksi'] = 1

                                if map_nom != "-- Abaikan / Tidak Ada --": df_file_filtered['Nilai_Bulanan'] = df_to_process[map_nom].apply(aman_ke_angka)
                                else: df_file_filtered['Nilai_Bulanan'] = 0

                                df_file_filtered['Bank Utama'] = df_file_filtered.apply(kategori_bank, axis=1)
                                df_file_filtered['Nama BPR Bersih'] = df_file_filtered['Provider_Asli'].apply(bersihkan_nama_bpr)

                                semua_data_terpilih.append(df_file_filtered)

            except Exception as e:
                st.error(f"❌ Gagal memproses {file.name}: {e}")
                continue

        # ==============================================================================
        # 🚀 TABEL MASTER GABUNGAN (DYNAMIC PIVOT ENGINE)
        # ==============================================================================
        if semua_data_terpilih and any(not df.empty for df in semua_data_terpilih):
            df_raw = pd.concat(semua_data_terpilih, ignore_index=True)

            df_raw["Bulan"] = df_raw["Bulan"].astype(str).str.strip().str.capitalize()
            df_raw["Bulan"] = pd.Categorical(df_raw["Bulan"], categories=URUTAN_BULAN, ordered=True)

            st.markdown("---")
            st.markdown("### 🎛️ Tampilan Tabel Master (Hasil Gabungan)")

            col_m1, col_m2 = st.columns(2)
            with col_m1:
                opsi_metrik = {
                    "Jumlah VA (Create)": "Jumlah VA",
                    "Jumlah Transaksi": "Jumlah Transaksi",
                    "Nominal Transaksi": "Nilai_Bulanan"
                }
                semua_pilihan = list(opsi_metrik.keys())
                saved_metrik = get_widget(nama_menu, "metrik_pivot", semua_pilihan)
                metrik_terpilih = st.multiselect("📊 1. Pilih Data Bulanan (Pivot):", options=semua_pilihan, default=saved_metrik, key="rek_metrik_pivot")
                set_widget(nama_menu, "metrik_pivot", metrik_terpilih)

            with col_m2:
                daftar_bank_unik = sorted(df_raw['Bank Utama'].astype(str).unique().tolist())
                saved_bank_sel = get_widget(nama_menu, "master_bank", daftar_bank_unik)
                valid_bank_sel = [b for b in saved_bank_sel if b in daftar_bank_unik]
                bank_master_terpilih = st.multiselect("🏦 2. Filter Kategori Bank:", options=daftar_bank_unik, default=valid_bank_sel, key="rek_master_bank")
                set_widget(nama_menu, "master_bank", bank_master_terpilih)

            if not metrik_terpilih:
                st.warning("⚠️ Silakan pilih minimal satu metrik untuk di-pivot (VA/Trx/Nominal).")
            elif not bank_master_terpilih:
                st.warning("⚠️ Silakan pilih minimal satu Bank.")
            else:
                df_raw_filtered = df_raw[df_raw['Bank Utama'].isin(bank_master_terpilih)]
                
                list_bpr_unik = sorted(df_raw_filtered['Nama BPR Bersih'].unique().tolist())
                
                with st.expander("🔍 Filter & Cari Nama BPR", expanded=True):
                    
                    if not list_bpr_unik:
                        st.info("👈 Silakan pilih minimal 1 Bank Utama terlebih dahulu agar daftar BPR muncul.")
                        bpr_terpilih = [] 
                    else:
                        st.markdown("💡 **Tips Pencarian:** Arahkan mouse ke **dalam area tabel** di bawah, lalu klik ikon **🔍 (Search)** yang otomatis muncul di **pojok kanan atas tabel**.")
                        
                        # SUDAH TRUE OTOMATIS:
                        pilih_semua = st.checkbox("☑️ Pilih Semua BPR", value=True)
                        
                        df_bpr_checkbox = pd.DataFrame({
                            "Pilih": [pilih_semua] * len(list_bpr_unik),
                            "Nama BPR Bersih": list_bpr_unik
                        })
                        
                        tabel_bpr_diedit = st.data_editor(
                            df_bpr_checkbox,
                            hide_index=True,
                            use_container_width=True,
                            height=250, 
                            column_config={
                                "Pilih": st.column_config.CheckboxColumn("Pilih", width="small"),
                                "Nama BPR Bersih": st.column_config.TextColumn("Nama BPR Bersih", disabled=True)
                            }
                        )
                        
                        bpr_terpilih = tabel_bpr_diedit[tabel_bpr_diedit["Pilih"] == True]["Nama BPR Bersih"].tolist()

                df_raw_filtered = df_raw_filtered[df_raw_filtered['Nama BPR Bersih'].isin(bpr_terpilih)]
                
                if df_raw_filtered.empty:
                    st.warning("⚠️ Tidak ada BPR yang dipilih. Silakan centang minimal satu BPR di menu filter.")
                else:
                    dict_df_metrik = {}
                    
                    for m_name in metrik_terpilih:
                        m_col = opsi_metrik[m_name]
                        
                        # 1. Total Samping (TOTAL)
                        df_agregasi = df_raw_filtered.groupby(["Bank Utama", "Nama BPR Bersih"], as_index=False)[m_col].sum()
                        df_agregasi = df_agregasi.rename(columns={m_col: "TOTAL"})
                        
                        # 2. Pivot Bulanan
                        df_pivot = df_raw_filtered.pivot_table(
                            index=["Bank Utama", "Nama BPR Bersih"],
                            columns="Bulan",
                            values=m_col,
                            aggfunc="sum",
                            fill_value=0
                        )
                        
                        for bln in URUTAN_BULAN:
                            if bln not in df_pivot.columns:
                                df_pivot[bln] = 0
                                
                        df_pivot = df_pivot[URUTAN_BULAN].reset_index()
                        
                        # 3. Gabung & Simpan 
                        df_final_metrik = pd.merge(df_pivot, df_agregasi, on=["Bank Utama", "Nama BPR Bersih"], how="left")
                        
                        df_final_metrik = df_final_metrik.fillna(0)
                        df_final_metrik.insert(0, "No", range(1, len(df_final_metrik) + 1))
                        
                        dict_df_metrik[m_name] = df_final_metrik

                # User bebas milih hide/show kolom finalnya + URUTANNYA
                contoh_df = dict_df_metrik[metrik_terpilih[0]]
                daftar_kolom_asli = contoh_df.columns.tolist()
                
                # JURUS AUTO-SORT (CUSTOM REQUEST BOS): 
                # Urutan: No -> Bulan -> Nama BPR -> Bank Utama -> TOTAL
                kolom_no = ['No'] if 'No' in daftar_kolom_asli else []
                kolom_identitas = ['Nama BPR Bersih', 'Bank Utama'] # Sengaja BPR dulu baru Bank
                
                # Cari kolom bulan (Semua kolom selain No, BPR, Bank, dan Total)
                kolom_bulan = [c for c in daftar_kolom_asli if c not in (['No', 'Nama BPR Bersih', 'Bank Utama', 'TOTAL'])]
                
                kolom_total = ['TOTAL'] if 'TOTAL' in daftar_kolom_asli else []
                
                # GABUNGKAN SESUAI URUTAN REQUEST
                urutan_ideal = kolom_no + kolom_bulan + [c for c in kolom_identitas if c in daftar_kolom_asli] + kolom_total
                
                saved_kol_sel = get_widget(nama_menu, "master_kolom", urutan_ideal)
                valid_kol_sel = [c for c in saved_kol_sel if c in urutan_ideal]
                
                # Ubah judulnya biar atasan paham cara kerjanya
                kolom_master_terpilih = st.multiselect(
                    "👁️ 3. Tampilkan & Urutkan Kolom (Hapus lalu pilih ulang untuk mengubah posisi):", 
                    options=urutan_ideal, 
                    default=valid_kol_sel, 
                    key="rek_master_kolom"
                )
                set_widget(nama_menu, "master_kolom", kolom_master_terpilih)

                if not kolom_master_terpilih:
                    st.warning("⚠️ Silakan pilih minimal satu Kolom untuk ditampilkan.")
                else:
                    # ==================================
                    # TAMPILAN UI (PAKAI STREAMLIT TABS)
                    # ==================================
                    tabs = st.tabs([f"📊 {m}" for m in metrik_terpilih])
                    
                    for idx, m_name in enumerate(metrik_terpilih):
                        with tabs[idx]:
                            df_tampil = dict_df_metrik[m_name][kolom_master_terpilih].copy()
                            
                            kolom_angka = [c for c in df_tampil.columns if c not in ('No', 'Bank Utama', 'Nama BPR Bersih')]
                            for kolom in kolom_angka:
                                df_tampil[kolom] = df_tampil[kolom].apply(format_ribuan)

                            st.dataframe(df_tampil, use_container_width=True, hide_index=True)
                            
                    st.markdown("---")

                    # ==================================
                    # EXPORT EXCEL MULTI-SHEET MAGIC 🪄
                    # ==================================
                    try:
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                            workbook = writer.book
                            format_angka_excel = workbook.add_format({'num_format': '#,##0'})

                            sheet_name_map = {
                                "Jumlah VA (Create)": "Jumlah VA",
                                "Jumlah Transaksi": "Jumlah Trx",
                                "Nominal Transaksi": "Nominal"
                            }

                            for m_name in metrik_terpilih:
                                df_excel = dict_df_metrik[m_name][kolom_master_terpilih]
                                nama_sheet = sheet_name_map.get(m_name, m_name[:30])
                                
                                df_excel.to_excel(writer, sheet_name=nama_sheet, index=False)
                                worksheet = writer.sheets[nama_sheet]
                                
                                for idx, kolom in enumerate(df_excel.columns):
                                    if kolom in ('Bank Utama', 'Nama BPR Bersih'):
                                        worksheet.set_column(idx, idx, 25)
                                    elif kolom == 'No':
                                        worksheet.set_column(idx, idx, 5)
                                    else:
                                        worksheet.set_column(idx, idx, 15, format_angka_excel)

                        st.download_button(
                            label="📥 Download Excel Multi-Sheet",
                            data=buffer.getvalue(),
                            file_name="Rekap_Data_Master_MultiSheet.xlsx",
                            mime="application/vnd.ms-excel",
                            type="primary",
                        )
                        
                        # ==========================================
                        # FITUR BARU: KIRIM DATA KE TAS RANSEL 🎒
                        # ==========================================
                        st.markdown("### 🚀 Opsi Lanjutan")
                        if st.button("📊 Bawa Data Ini ke Menu Visualisasi", type="primary", use_container_width=True):
                            st.session_state['data_rekap_visual'] = df_raw_filtered.copy()
                            st.success("✅ Data berhasil diamankan! Silakan klik menu **'Analisis Persentase'** di navigasi sebelah kiri.")

                    except ModuleNotFoundError:
                        st.error("🚨 Library 'xlsxwriter' belum terinstall!")
        else:
            st.warning("Data masih kosong.")


# ==============================================================================
# JALUR 2 : LAPORAN MBANKING (ULTIMATE - UI COLOR & EXCEL FORMAT)
# ==============================================================================
if menu_terpilih == "Laporan MBanking (Matrix 1-31)":
    nama_menu = 'mbanking'
    st.title("🔄 Modul Laporan MBanking (Matrix 1-31)")
    st.markdown("Upload laporan dari portal bank (PDF/CSV/Excel). Ketik identitas manual, jadikan Pivot 1-31!")
    st.markdown("---")

    col_up1, col_up2 = st.columns([4, 1])

    with col_up1:
        uploaded_files = st.file_uploader(
            "Upload Laporan", 
            accept_multiple_files=True, 
            key=f"uploader_rekap_{st.session_state['file_uploader_key']}" 
        )

    with col_up2:
        st.write("") 
        st.write("")
        if st.button("🗑️ Bersihkan File", key="btn_clear_rekap", use_container_width=True):
            st.session_state["file_uploader_key"] += 1
            st.session_state.state_data[nama_menu]['file_cache'] = []
            st.rerun()

    if uploaded_files:
        st.session_state.state_data[nama_menu]['file_cache'] = [{'name': f.name, 'size': f.size, 'data': f.getvalue()} for f in uploaded_files]
    else:
        st.session_state.state_data[nama_menu]['file_cache'] = []

    active_files = st.session_state.state_data[nama_menu]['file_cache']

    if active_files:
        semua_data_terstandar = []
        st.markdown("### 🍳 Dapur Pemetaan Kolom (Column Mapping)")
        
        # Palet warna untuk ngebedain tiap kotak file (Req #1)
        warna_palette = ['#FF4B4B', '#00b894', '#0984e3', '#fdcb6e', '#6c5ce7', '#e84393']
        idx_warna = 0

        for f_dict in active_files:
            file = CachedFile(f_dict['name'], f_dict['size'], f_dict['data'])
            file_id = f"{file.name}_{file.size}"
            
            try:
                data_sumber_mbk = []
                if file.name.endswith('.pdf'):
                    teks_full = ekstrak_teks_pdf(file.getvalue())
                    raw_data = parse_baris_transaksi(teks_full)
                    df_file = pd.DataFrame(raw_data)
                    if not df_file.empty:
                        df_file = df_file[~df_file['Provider_Asli'].astype(str).str.lower().str.contains('total', na=False)]
                        data_sumber_mbk.append((file.name, df_file))
                elif file.name.endswith('.csv'):
                    df_file = baca_csv_pintar(file)
                    data_sumber_mbk.append((file.name, df_file))
                elif file.name.endswith('.xlsx'):
                    xls_mbk = pd.ExcelFile(io.BytesIO(file.getvalue()))
                    sheets_mbk = xls_mbk.sheet_names
                    
                    saved_sh_mbk = get_widget(nama_menu, f"mbk_sheets_{file_id}", sheets_mbk)
                    valid_sh_mbk = [s for s in saved_sh_mbk if s in sheets_mbk]
                    
                    with st.expander(f"📑 Pilih Sheet Excel: {file.name}", expanded=True):
                        pilih_sh_mbk = st.multiselect("Centang Sheet:", options=sheets_mbk, default=valid_sh_mbk, key=f"rek_mbk_sh_{file_id}")
                        set_widget(nama_menu, f"mbk_sheets_{file_id}", pilih_sh_mbk)
                        
                    for sh in pilih_sh_mbk:
                        df_temp = pd.read_excel(xls_mbk, sheet_name=sh)
                        if not df_temp.empty: data_sumber_mbk.append((f"{file.name} [{sh}]", df_temp))

                for nama_sumber, df_file in data_sumber_mbk:
                    sumber_id = f"{file_id}_{nama_sumber}"
                    if df_file is None or df_file.empty: continue
                    
                    # REQ 1: INJEKSI WARNA BORDER/TOPI KHUSUS TIAP KOTAK 🎨
                    warna_pilihan = warna_palette[idx_warna % len(warna_palette)]
                    idx_warna += 1
                    
                    # Garis warna tebal di atas kotak
                    st.markdown(f'<div style="height: 10px; background-color: {warna_pilihan}; border-radius: 5px 5px 0 0; margin-bottom: -1rem; z-index: 99; position: relative;"></div>', unsafe_allow_html=True)

                    with st.expander(f"📄 Pengaturan Data: {nama_sumber}", expanded=True):
                        
                        tabel_preview = st.empty()
                        opsi_kolom = list(df_file.columns)

                        st.markdown("**1. Identitas File (Input Manual)**")
                        col_id1, col_id2 = st.columns(2)
                        with col_id1:
                            saved_man_bank = get_widget(nama_menu, f"man_bank_{sumber_id}", "")
                            manual_bank = st.text_input("📱 Masukkan Nama Aplikasi (Bank):", value=saved_man_bank, key=f"mbk_man_bank_{sumber_id}")
                            set_widget(nama_menu, f"man_bank_{sumber_id}", manual_bank)
                        with col_id2:
                            saved_man_mitra = get_widget(nama_menu, f"man_mitra_{sumber_id}", "")
                            manual_mitra = st.text_input("🏢 Masukkan Nama Client / Mitra:", value=saved_man_mitra, key=f"mbk_man_mitra_{sumber_id}")
                            set_widget(nama_menu, f"man_mitra_{sumber_id}", manual_mitra)

                        st.markdown("**2. Petakan Kolom Data (Tanggal, Angka, & Status)**")
                        col1, col2, col3, col4 = st.columns(4)

                        with col1:
                            default_tgl = next((c for c in opsi_kolom if 'tanggal' in c.lower() or 'date' in c.lower()), None)
                            saved_tgl = get_widget(nama_menu, f"tgl_{sumber_id}", default_tgl)
                            idx_tgl = opsi_kolom.index(saved_tgl) if saved_tgl in opsi_kolom else None
                            map_tgl = st.selectbox("📅 Tanggal", options=opsi_kolom, index=idx_tgl, placeholder="Ketik/Pilih...", key=f"mbk_tgl_{sumber_id}")
                            set_widget(nama_menu, f"tgl_{sumber_id}", map_tgl)
                            
                        with col2:
                            saved_trx = get_widget(nama_menu, f"trx_{sumber_id}", None)
                            idx_trx = opsi_kolom.index(saved_trx) if saved_trx in opsi_kolom else None
                            map_trx = st.selectbox("🔢 Jml Transaksi", options=opsi_kolom, index=idx_trx, placeholder="Ketik/Pilih...", key=f"mbk_trx_{sumber_id}")
                            set_widget(nama_menu, f"trx_{sumber_id}", map_trx)
                            
                        with col3:
                            saved_nom = get_widget(nama_menu, f"nom_{sumber_id}", None)
                            idx_nom = opsi_kolom.index(saved_nom) if saved_nom in opsi_kolom else None
                            map_nom = st.selectbox("💰 Nominal Trx", options=opsi_kolom, index=idx_nom, placeholder="Ketik/Pilih...", key=f"mbk_nom_{sumber_id}")
                            set_widget(nama_menu, f"nom_{sumber_id}", map_nom)
                            
                        with col4:
                            default_stat = next((c for c in opsi_kolom if 'status' in c.lower() or 'ket' in c.lower()), None)
                            saved_stat = get_widget(nama_menu, f"stat_{sumber_id}", default_stat)
                            idx_stat = opsi_kolom.index(saved_stat) if saved_stat in opsi_kolom else None
                            map_status = st.selectbox("🚦 Status (Opsional)", options=opsi_kolom, index=idx_stat, placeholder="Ketik/Pilih...", key=f"mbk_stat_{sumber_id}")
                            set_widget(nama_menu, f"stat_{sumber_id}", map_status)

                        kolom_terpilih = [c for c in [map_tgl, map_trx, map_nom, map_status] if c is not None]
                        
                        if kolom_terpilih:
                            def highlight_cols(x):
                                return ['background-color: #00b894; color: white; font-weight: bold' if x.name in kolom_terpilih else '' for _ in x]
                            tabel_preview.dataframe(df_file.head(30).style.apply(highlight_cols, axis=0), use_container_width=True)
                        else:
                            tabel_preview.dataframe(df_file.head(30), use_container_width=True)

                        df_to_process = df_file.copy()

                        if map_status is not None:
                            unique_status = df_to_process[map_status].astype(str).unique().tolist()
                            saved_fil = get_widget(nama_menu, f"fil_{sumber_id}", unique_status)
                            valid_saved_fil = [s for s in saved_fil if s in unique_status]
                            selected_statuses = st.multiselect("✔️ Pilih Status yang Dihitung:", options=unique_status, default=valid_saved_fil, key=f"mbk_fil_{sumber_id}")
                            set_widget(nama_menu, f"fil_{sumber_id}", selected_statuses)
                            df_to_process = df_to_process[df_to_process[map_status].astype(str).isin(selected_statuses)]

                        df_std = pd.DataFrame(index=df_to_process.index)

                        if manual_mitra.strip() == "": df_std['Nama Mitra'] = "Mitra Belum Diisi"
                        else: df_std['Nama Mitra'] = manual_mitra.strip()

                        if manual_bank.strip() == "": df_std['Bank Utama'] = "Aplikasi Belum Diisi"
                        else: df_std['Bank Utama'] = manual_bank.strip()

                        if map_tgl is not None:
                            tanggal_series = parse_tanggal_pintar(df_to_process[map_tgl])
                            df_std['Hari'] = tanggal_series.dt.day.fillna(0).astype(int)
                        else:
                            df_std['Hari'] = 0

                        if map_trx is not None: df_std['Jml Transaksi'] = df_to_process[map_trx].apply(aman_ke_angka)
                        else: df_std['Jml Transaksi'] = 1

                        if map_nom is not None: df_std['Nominal'] = df_to_process[map_nom].apply(aman_ke_angka)
                        else: df_std['Nominal'] = 0

                        semua_data_terstandar.append(df_std)

            except Exception as e:
                st.error(f"❌ Gagal memproses {file.name}: {e}")
                continue

        if semua_data_terstandar:
            df_master = pd.concat(semua_data_terstandar, ignore_index=True)

            st.markdown("---")
            st.markdown("### 📊 Pivot Report Bulanan (Matrix 1-31)")

            hari_kolom = [i for i in range(1, 32)]
            pivot_trx = df_master.pivot_table(index=['Bank Utama', 'Nama Mitra'], columns='Hari', values='Jml Transaksi', aggfunc='sum', fill_value=0).reset_index()
            pivot_nom = df_master.pivot_table(index=['Bank Utama', 'Nama Mitra'], columns='Hari', values='Nominal', aggfunc='sum', fill_value=0).reset_index()

            for h in hari_kolom:
                if h not in pivot_trx.columns: pivot_trx[h] = 0
                if h not in pivot_nom.columns: pivot_nom[h] = 0

            kolom_rapi = ['Bank Utama', 'Nama Mitra'] + hari_kolom
            pivot_trx = pivot_trx[kolom_rapi]
            pivot_nom = pivot_nom[kolom_rapi]

            pivot_trx['TOTAL'] = pivot_trx[hari_kolom].sum(axis=1)
            pivot_nom['TOTAL'] = pivot_nom[hari_kolom].sum(axis=1)

            tab1, tab2 = st.tabs(["📉 Jumlah Transaksi", "💰 Nominal Transaksi"])

            with tab1:
                tampil_trx = pivot_trx.copy()
                for col in hari_kolom + ['TOTAL']: tampil_trx[col] = tampil_trx[col].apply(format_ribuan)
                st.dataframe(tampil_trx, use_container_width=True, hide_index=True)

            with tab2:
                tampil_nom = pivot_nom.copy()
                for col in hari_kolom + ['TOTAL']: tampil_nom[col] = tampil_nom[col].apply(format_ribuan)
                st.dataframe(tampil_nom, use_container_width=True, hide_index=True)

            st.markdown("### 📥 Download Laporan")
            
            # =======================================================
            # REQ 6: CUSTOM EXPORT EXCEL SESUAI REFERENSI GAMBAR BOS 🪄
            # =======================================================
            try:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    workbook = writer.book
                    
                    # 1. Bikin Style Excel yang ciamik
                    fmt_title = workbook.add_format({'bold': True, 'font_size': 11})
                    
                    # Warna header kuning persis gambar (#FFC000)
                    fmt_header = workbook.add_format({
                        'bold': True, 'bg_color': '#FFC000', 'border': 1, 
                        'align': 'center', 'valign': 'vcenter'
                    })
                    
                    fmt_data_teks = workbook.add_format({'border': 1, 'valign': 'vcenter'})
                    fmt_data_angka = workbook.add_format({'border': 1, 'num_format': '#,##0', 'align': 'center', 'valign': 'vcenter'})
                    
                    fmt_total_teks = workbook.add_format({'bold': True, 'border': 1, 'align': 'center', 'valign': 'vcenter'})
                    fmt_total_angka = workbook.add_format({'bold': True, 'border': 1, 'num_format': '#,##0', 'align': 'center', 'valign': 'vcenter'})

                    # 2. Siapin data dinamis Tanggal/Bulan
                    bln_ini = datetime.now().month
                    thn_ini = datetime.now().year
                    nama_bln_indo = ["JANUARI", "FEBRUARI", "MARET", "APRIL", "MEI", "JUNI", "JULI", "AGUSTUS", "SEPTEMBER", "OKTOBER", "NOVEMBER", "DESEMBER"]
                    teks_bulan_laporan = f": {nama_bln_indo[bln_ini - 1]} {thn_ini}"

                    data_exports = [('Jml Trx', pivot_trx), ('Nominal Trx', pivot_nom)]

                    # 3. Tulis data ke masing-masing Sheet
                    for sheet_name, df_export in data_exports:
                        worksheet = workbook.add_worksheet(sheet_name)
                        
                        # Baris 4 (Index 3): BULAN
                        worksheet.write(3, 0, "BULAN", fmt_title)
                        worksheet.write(3, 1, teks_bulan_laporan, fmt_title)
                        
                        # Baris 5 & 6 (Index 4 & 5): HEADER MERGE
                        worksheet.merge_range(4, 0, 5, 0, "NO.", fmt_header)
                        worksheet.merge_range(4, 1, 5, 1, "NAMA APLIKASI", fmt_header)
                        worksheet.merge_range(4, 2, 5, 2, "NAMA CLIENT", fmt_header)
                        
                        # Merge Header TANGGAL (Di atas angka 1-31)
                        jml_hari = len(hari_kolom)
                        worksheet.merge_range(4, 3, 4, 3 + jml_hari - 1, "TANGGAL", fmt_header)
                        worksheet.merge_range(4, 3 + jml_hari, 5, 3 + jml_hari, "TOTAL", fmt_header)
                        
                        # Tulis angka 1-31 di Baris 6 (Index 5)
                        for i, hari in enumerate(hari_kolom):
                            worksheet.write(5, 3 + i, hari, fmt_header)
                            
                        # Baris 7 dst (Index 6): TULIS DATA
                        row_start = 6
                        for idx, row in df_export.iterrows():
                            worksheet.write(row_start + idx, 0, idx + 1, fmt_data_angka) # Kolom No
                            worksheet.write(row_start + idx, 1, row['Bank Utama'], fmt_data_teks)
                            worksheet.write(row_start + idx, 2, row['Nama Mitra'], fmt_data_teks)
                            
                            # Tulis harian 1-31
                            for i, hari in enumerate(hari_kolom):
                                worksheet.write(row_start + idx, 3 + i, row[hari], fmt_data_angka)
                                
                            # Tulis Total baris
                            worksheet.write(row_start + idx, 3 + jml_hari, row['TOTAL'], fmt_data_angka)
                                
                        # Baris Paling Bawah: JUMLAH KESELURUHAN
                        last_row = row_start + len(df_export)
                        worksheet.merge_range(last_row, 0, last_row, 2, "JUMLAH", fmt_total_teks)
                        
                        for i, hari in enumerate(hari_kolom):
                            total_hari_ini = df_export[hari].sum()
                            worksheet.write(last_row, 3 + i, total_hari_ini, fmt_total_angka)
                            
                        # Total dari kolom TOTAL
                        grand_total = df_export['TOTAL'].sum()
                        worksheet.write(last_row, 3 + jml_hari, grand_total, fmt_total_angka)
                            
                        # Lebarin kolom biar rapi otomatis
                        worksheet.set_column(0, 0, 5)   # NO
                        worksheet.set_column(1, 1, 18)  # APLIKASI
                        worksheet.set_column(2, 2, 35)  # CLIENT
                        worksheet.set_column(3, 3 + jml_hari, 6) # TANGGAL 1-31 & TOTAL

                st.download_button(
                    label="📥 Download Master Excel (Sesuai Referensi)",
                    data=buffer.getvalue(),
                    file_name="Laporan_Pivot_Mbanking.xlsx",
                    mime="application/vnd.ms-excel",
                    type="primary",
                )
            except ModuleNotFoundError:
                st.error("🚨 Library 'xlsxwriter' belum terinstall!")


# =====================================================================
# BLOK MENU BARU: ANALISIS PERSENTASE (FULL VERSION - ANTI ERROR)
# =====================================================================
if menu_terpilih == "Analisis Persentase":
    st.title("📊 Analisis & Penghitung Persentase")
    st.markdown("Buat visualisasi dinamis dari data Rekap Master atau upload file baru.")

    # 1. Cek apakah ada data di "tas ransel"
    ada_data_rekap = 'data_rekap_visual' in st.session_state
    
    # 2. Bikin opsi sumber data (Otomatis muncul opsi tas ransel kalau datanya ada)
    opsi_sumber = ["Upload File Baru 📂"]
    if ada_data_rekap:
        opsi_sumber.insert(0, "Gunakan Data dari Rekap Master 🎒")
        
    sumber_data = st.radio("Pilih Sumber Data:", opsi_sumber, horizontal=True)
    st.write("---")
    
    df_chart = None # Variabel penampung tabel akhir
    nama_kolom_nilai = "Total Transaksi" # Default sumbu Y
    
    # ==========================================
    # LOGIKA 1: JIKA PAKAI DATA DARI REKAP MASTER
    # ==========================================
    if sumber_data == "Gunakan Data dari Rekap Master 🎒":
        st.info("📦 Menggunakan data hasil filter dari menu Laporan VA.")
        df_mentah = st.session_state['data_rekap_visual']
        
        # Karena data rekap master bulannya ke samping, kita Unpivot (Melt) juga biar rapi
        bulan_cols = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember']
        bulan_yg_ada = [b for b in bulan_cols if b in df_mentah.columns]
        
        if bulan_yg_ada:
            nama_kolom_nilai = "Total Data Rekap"
            # Cek nama kolom BPR-nya (menyesuaikan dengan di rekap master kamu)
            kolom_bpr = 'Nama BPR Bersih' if 'Nama BPR Bersih' in df_mentah.columns else 'Nama BPR'
            
            df_chart = pd.melt(df_mentah, id_vars=['Bank Utama', kolom_bpr], 
                               value_vars=bulan_yg_ada,
                               var_name='Bulan', value_name=nama_kolom_nilai)
        else:
            df_chart = df_mentah
            
    # ==========================================
    # LOGIKA 2: JIKA UPLOAD FILE BARU (+ MESIN CUCI V2.0)
    # ==========================================
    else:
        file_persentase = st.file_uploader("Upload File (CSV/Excel)", type=['csv', 'xlsx', 'xls'], key="upload_persentase")
        if file_persentase:
            try:
                # A. Baca File & Pilih Sheet
                if file_persentase.name.endswith('.csv'):
                    df_raw = pd.read_csv(file_persentase, header=None)
                    st.info("📄 Format CSV terdeteksi.")
                    nama_kolom_nilai = "Total Nilai" 
                else:
                    xls = pd.ExcelFile(file_persentase)
                    daftar_sheet = xls.sheet_names
                    sheet_terpilih = st.selectbox("📑 Pilih Sheet yang ingin dianalisis:", daftar_sheet)
                    df_raw = pd.read_excel(xls, sheet_name=sheet_terpilih, header=None)
                    nama_kolom_nilai = sheet_terpilih 

                # B. Deteksi 1: Laporan Bertumpuk VA Bank
                if len(df_raw.columns) > 1 and (pd.isna(df_raw.iloc[0, 1]) or str(df_raw.iloc[0, 0]).startswith("VA ")):
                    st.info("🛠️ File memiliki format 'Laporan Bertumpuk VA'. Sistem sedang memutar data...")
                    df_raw['Bank Utama'] = df_raw.iloc[:, 0].apply(
                        lambda x: str(x).strip() if str(x).strip().startswith('VA ') else None
                    ).ffill()
                    df_clean = df_raw.dropna(subset=[1])
                    df_clean = df_clean[~df_clean[1].astype(str).str.contains("Nama BPR", case=False, na=False)]
                    df_final = pd.DataFrame({'Bank Utama': df_clean['Bank Utama'], 'Nama BPR': df_clean[1]})
                    
                    bulan_cols = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember']
                    for i, bln in enumerate(bulan_cols):
                        if (i+2) < len(df_clean.columns): 
                            df_final[bln] = pd.to_numeric(df_clean.iloc[:, i+2], errors='coerce').fillna(0)
                            
                    df_chart = pd.melt(df_final, id_vars=['Bank Utama', 'Nama BPR'], 
                                       value_vars=[b for b in bulan_cols if b in df_final.columns],
                                       var_name='Bulan', value_name=nama_kolom_nilai)

                # C. Deteksi 2: Laporan Bertumpuk PERIODE Tahun
                elif len(df_raw.columns) > 1 and df_raw[0].astype(str).str.contains("PERIODE", case=False, na=False).any():
                    st.info("🛠️ Terdeteksi format 'Rincian Pertahun (Periode)'. Sistem sedang menyaring data...")
                    df_raw['Periode Tahun'] = df_raw.iloc[:, 0].apply(
                        lambda x: str(x).strip() if "PERIODE" in str(x).upper() else None
                    ).ffill()
                    df_clean = df_raw.dropna(subset=[1])
                    df_clean = df_clean[~df_clean[1].astype(str).str.contains("Nama BPR", case=False, na=False)]
                    df_final = pd.DataFrame({'Periode Tahun': df_clean['Periode Tahun'], 'Nama BPR': df_clean[1]})
                    
                    bulan_cols = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember']
                    for i, bln in enumerate(bulan_cols):
                        if (i+2) < len(df_clean.columns): 
                            df_final[bln] = pd.to_numeric(df_clean.iloc[:, i+2], errors='coerce').fillna(0)
                            
                    df_chart = pd.melt(df_final, id_vars=['Periode Tahun', 'Nama BPR'], 
                                       value_vars=[b for b in bulan_cols if b in df_final.columns],
                                       var_name='Bulan', value_name=nama_kolom_nilai)

                # D. Deteksi 3: Format Normal
                else:
                    st.info("📄 Format tabel standar terdeteksi.")
                    df_chart = df_raw.copy()
                    df_chart.columns = df_chart.iloc[0]
                    df_chart = df_chart[1:].reset_index(drop=True)

            except Exception as e:
                st.error(f"Gagal memproses file. Pastikan format tabelnya rapi. Detail Error: {e}")

    # ==========================================
    # LOGIKA 3: GAMBAR GRAFIKNYA 
    # ==========================================
    if df_chart is not None and not df_chart.empty:
        col_x, col_y, col_type = st.columns(3)
        
        with col_x:
            kolom_x = st.selectbox("👉 Pilih Sumbu X (Kategori/Label):", df_chart.columns)
            
        with col_y:
            kolom_angka = df_chart.select_dtypes(include=['number']).columns.tolist()
            if not kolom_angka:
                st.error("Gawat! Tidak ada kolom berisi angka untuk dihitung.")
            else:
                kolom_y = st.selectbox("📈 Pilih Sumbu Y (Angka/Nilai):", kolom_angka)
                
        with col_type:
            tipe_grafik = st.selectbox("🎨 Pilih Jenis Diagram:", ["Batang (Bar Chart)", "Garis (Line Chart)", "Lingkar (Pie Chart)"])
            
        if kolom_angka:
            df_grouped = df_chart.groupby(kolom_x)[kolom_y].sum().reset_index()
            
            st.markdown(f"### 📈 Visualisasi {kolom_y} berdasarkan {kolom_x}")
            
            if tipe_grafik == "Batang (Bar Chart)":
                fig = px.bar(df_grouped, x=kolom_x, y=kolom_y, text_auto=',.0f', 
                             color=kolom_x, 
                             title=f"Total {kolom_y}")
            
            elif tipe_grafik == "Garis (Line Chart)":
                fig = px.line(df_grouped, x=kolom_x, y=kolom_y, markers=True, 
                              title=f"Tren {kolom_y}")
            
            else: 
                fig = px.pie(df_grouped, names=kolom_x, values=kolom_y, 
                             hole=0.4, 
                             title=f"Porsi {kolom_y}")
                fig.update_traces(textposition='inside', textinfo='percent+label')
                
            st.plotly_chart(fig, use_container_width=True)
            
            st.markdown("### 📋 Tabel Rincian & Persentase")
            
            total_y = df_grouped[kolom_y].sum()
            df_grouped['Persentase (%)'] = (df_grouped[kolom_y] / total_y) * 100
            df_grouped['Persentase (%)'] = df_grouped['Persentase (%)'].round(2).astype(str) + " %"
            
            baris_total = pd.DataFrame({
                kolom_x: ['🎯 TOTAL KESELURUHAN'],
                kolom_y: [total_y],
                'Persentase (%)': ['100.00 %']
            })
            
            df_final = pd.concat([df_grouped, baris_total], ignore_index=True)
            st.dataframe(df_final, use_container_width=True, hide_index=True)

# ==============================================================================
# JALUR 3 : LAPORAN QRIS (ULTIMATE)
# ==============================================================================
if menu_terpilih == "Laporan Qris":
    st.title("📱 Modul Laporan QRIS")
    st.markdown("Upload Laporan Invoice QRIS. Sistem otomatis merakit Pivot Jumlah, Nominal, dan Fee MSO!")
    st.markdown("---")

    uploaded_qris = st.file_uploader("Upload Laporan Invoice QRIS (Excel)", type=['xlsx', 'xls'], accept_multiple_files=True, key="upload_qris")

    if uploaded_qris:
        semua_data_qris = []
        
        with st.spinner("Sedang mencuci dan merakit data QRIS..."):
            for file in uploaded_qris:
                try:
                    df = pd.read_excel(file)
                    
                    if 'Nama Lembaga' in df.columns and 'Tanggal Transaksi' in df.columns and 'Nominal Transaksi' in df.columns and 'Nominal Fee MSO' in df.columns:
                        
                        df_bersih = df[['Nama Lembaga', 'Tanggal Transaksi', 'Nominal Transaksi', 'Nominal Fee MSO']].copy()
                        df_bersih['Tanggal Transaksi'] = pd.to_datetime(df_bersih['Tanggal Transaksi'], errors='coerce')
                        df_bersih['Bulan'] = df_bersih['Tanggal Transaksi'].dt.month.map(MAP_BULAN_ANGKA)
                        
                        semua_data_qris.append(df_bersih)
                    else:
                        st.warning(f"⚠️ File {file.name} diabaikan karena format kolomnya beda (Tidak ada Nama Lembaga/Tanggal/Nominal/Nominal Fee MSO).")
                
                except Exception as e:
                    st.error(f"❌ Gagal membaca {file.name}: {e}")

        # Jika ada data yang sukses dicuci
        if semua_data_qris:
            df_master_qris = pd.concat(semua_data_qris, ignore_index=True)
            df_master_qris = df_master_qris.dropna(subset=['Bulan'])
            
            # ==========================================
            # 1. PRATINJAU DATA MENTAH (PREVIEW)
            # ==========================================
            st.markdown("### 👁️ Pratinjau Data Invoice")
            with st.expander("Buka untuk melihat data gabungan QRIS", expanded=False):
                st.markdown("""
                    <style>
                    .outline-preview-qris {
                        border: 2px solid #00b894; 
                        border-radius: 10px;
                        padding: 10px;
                        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                        margin-bottom: 10px;
                    }
                    </style>
                    <div class="outline-preview-qris">
                """, unsafe_allow_html=True)
                
                st.dataframe(df_master_qris.head(100), use_container_width=True)
                st.markdown("</div>", unsafe_allow_html=True)

            # ==========================================
            # 2. FILTER BPR & FILTER BULAN (CHECKLIST)
            # ==========================================
            st.markdown("### 🔍 Filter Data (BPR & Bulan)")
            
            col_f1, col_f2 = st.columns(2)

            with col_f1:
                st.markdown("**Pilih BPR:**")
                list_bpr_unik = sorted(df_master_qris['Nama Lembaga'].dropna().unique().tolist())
                
                pilih_semua_bpr = st.checkbox("☑️ Pilih Semua BPR", value=True, key="qris_semua_bpr")
                df_bpr_checkbox = pd.DataFrame({
                    "Pilih": [pilih_semua_bpr] * len(list_bpr_unik),
                    "Nama BPR": list_bpr_unik
                })
                
                st.markdown("""<style>.outline-f1 { border: 2px solid #0984e3; border-radius: 10px; padding: 10px; }</style><div class="outline-f1">""", unsafe_allow_html=True)
                tabel_bpr_diedit = st.data_editor(
                    df_bpr_checkbox, hide_index=True, use_container_width=True, height=180,
                    column_config={"Pilih": st.column_config.CheckboxColumn("Pilih", width="small")}
                )
                st.markdown("</div>", unsafe_allow_html=True)
                bpr_terpilih = tabel_bpr_diedit[tabel_bpr_diedit["Pilih"] == True]["Nama BPR"].tolist()

            with col_f2:
                st.markdown("**Pilih Bulan:**")
                pilih_semua_bln = st.checkbox("☑️ Pilih Semua Bulan", value=True, key="qris_semua_bln")
                df_bln_checkbox = pd.DataFrame({
                    "Pilih": [pilih_semua_bln] * len(URUTAN_BULAN),
                    "Bulan": URUTAN_BULAN
                })
                
                st.markdown("""<style>.outline-f2 { border: 2px solid #e84393; border-radius: 10px; padding: 10px; }</style><div class="outline-f2">""", unsafe_allow_html=True)
                tabel_bln_diedit = st.data_editor(
                    df_bln_checkbox, hide_index=True, use_container_width=True, height=180,
                    column_config={"Pilih": st.column_config.CheckboxColumn("Pilih", width="small")}
                )
                st.markdown("</div>", unsafe_allow_html=True)
                bulan_terpilih = tabel_bln_diedit[tabel_bln_diedit["Pilih"] == True]["Bulan"].tolist()

            # Terapkan filter ke dataframe utama
            df_master_qris = df_master_qris[
                (df_master_qris['Nama Lembaga'].isin(bpr_terpilih)) & 
                (df_master_qris['Bulan'].isin(bulan_terpilih))
            ]

            if df_master_qris.empty:
                st.warning("⚠️ Data kosong. Pastikan minimal ada 1 BPR dan 1 Bulan yang dicentang!")
            else:
                st.markdown("### 📊 Hasil Pivot Laporan QRIS")

                # ==========================================
                # 3. MESIN PIVOT DATA
                # ==========================================
                pivot_jml = df_master_qris.pivot_table(index='Nama Lembaga', columns='Bulan', values='Nominal Transaksi', aggfunc='count', fill_value=0).reset_index()
                pivot_nom = df_master_qris.pivot_table(index='Nama Lembaga', columns='Bulan', values='Nominal Transaksi', aggfunc='sum', fill_value=0).reset_index()
                pivot_fee = df_master_qris.pivot_table(index='Nama Lembaga', columns='Bulan', values='Nominal Fee MSO', aggfunc='sum', fill_value=0).reset_index()

                for bln in URUTAN_BULAN:
                    if bln not in pivot_jml.columns: pivot_jml[bln] = 0
                    if bln not in pivot_nom.columns: pivot_nom[bln] = 0
                    if bln not in pivot_fee.columns: pivot_fee[bln] = 0

                kolom_rapi = ['Nama Lembaga'] + URUTAN_BULAN
                pivot_jml = pivot_jml[kolom_rapi]
                pivot_nom = pivot_nom[kolom_rapi]
                pivot_fee = pivot_fee[kolom_rapi]

                pivot_jml['Jumlah'] = pivot_jml[URUTAN_BULAN].sum(axis=1)
                pivot_nom['Jumlah'] = pivot_nom[URUTAN_BULAN].sum(axis=1)
                pivot_fee['Jumlah'] = pivot_fee[URUTAN_BULAN].sum(axis=1)

                pivot_jml = pivot_jml.rename(columns={'Nama Lembaga': 'Nama BPR'})
                pivot_nom = pivot_nom.rename(columns={'Nama Lembaga': 'Nama BPR'})
                pivot_fee = pivot_fee.rename(columns={'Nama Lembaga': 'Nama BPR'})

                pivot_jml.insert(0, 'No.', range(1, len(pivot_jml) + 1))
                pivot_nom.insert(0, 'No.', range(1, len(pivot_nom) + 1))
                pivot_fee.insert(0, 'No.', range(1, len(pivot_fee) + 1))

                # ==========================================
                # 4. TAMPILAN WEB (TAB)
                # ==========================================
                tab1, tab2, tab3 = st.tabs(["📉 Jumlah Transaksi", "💰 Nominal Transaksi", "💸 Fee MSO"])
                
                with tab1:
                    tampil_jml = pivot_jml.copy()
                    for col in URUTAN_BULAN + ['Jumlah']: tampil_jml[col] = tampil_jml[col].apply(format_ribuan)
                    st.dataframe(tampil_jml, use_container_width=True, hide_index=True)
                    
                with tab2:
                    tampil_nom = pivot_nom.copy()
                    for col in URUTAN_BULAN + ['Jumlah']: tampil_nom[col] = tampil_nom[col].apply(format_ribuan)
                    st.dataframe(tampil_nom, use_container_width=True, hide_index=True)

                with tab3:
                    tampil_fee = pivot_fee.copy()
                    for col in URUTAN_BULAN + ['Jumlah']: tampil_fee[col] = tampil_fee[col].apply(format_ribuan)
                    st.dataframe(tampil_fee, use_container_width=True, hide_index=True)
                    
                # ==========================================
                # 5. EXPORT EXCEL KHUSUS QRIS (3 SHEET)
                # ==========================================
                st.markdown("### 📥 Download Laporan")
                try:
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        workbook = writer.book
                        
                        fmt_title = workbook.add_format({'bold': True, 'font_size': 11})
                        fmt_header = workbook.add_format({'bold': True, 'border': 1, 'align': 'center', 'valign': 'vcenter'})
                        fmt_angka = workbook.add_format({'border': 1, 'num_format': '#,##0', 'align': 'center', 'valign': 'vcenter'})
                        fmt_teks = workbook.add_format({'border': 1, 'valign': 'vcenter'})
                        fmt_total = workbook.add_format({'bold': True, 'border': 1, 'num_format': '#,##0', 'align': 'center', 'valign': 'vcenter'})

                        sheets_data = [
                            ('Jumlah Transaksi', pivot_jml, 'JUMLAH TRANSAKSI QRIS BIMASAKTI (WINPAY)'), 
                            ('Nominal Transaksi', pivot_nom, 'NOMINAL TRANSAKSI QRIS BIMASAKTI (WINPAY)'),
                            ('Fee MSO', pivot_fee, 'NOMINAL FEE MSO QRIS BIMASAKTI (WINPAY)')
                        ]

                        for sheet_name, df_export, title in sheets_data:
                            df_export.to_excel(writer, sheet_name=sheet_name, startrow=2, index=False, header=False)
                            worksheet = writer.sheets[sheet_name]
                            
                            worksheet.write(0, 0, title, fmt_title)
                            
                            for col_num, value in enumerate(df_export.columns.values):
                                worksheet.write(2, col_num, value, fmt_header)

                            for row_num in range(len(df_export)):
                                worksheet.write(row_num + 3, 0, df_export.iloc[row_num, 0], fmt_angka) 
                                worksheet.write(row_num + 3, 1, df_export.iloc[row_num, 1], fmt_teks)  
                                for col_num in range(2, len(df_export.columns)):
                                    worksheet.write(row_num + 3, col_num, df_export.iloc[row_num, col_num], fmt_angka)

                            last_row = len(df_export) + 3
                            worksheet.merge_range(last_row, 0, last_row, 1, "JUMLAH", fmt_header)
                            for col_num in range(2, len(df_export.columns)):
                                total_val = df_export.iloc[:, col_num].sum()
                                worksheet.write(last_row, col_num, total_val, fmt_total)

                            worksheet.set_column(0, 0, 5)   
                            worksheet.set_column(1, 1, 35)  
                            worksheet.set_column(2, 14, 12) 
                            
                    st.download_button(
                        label="📥 Download Master Excel QRIS",
                        data=buffer.getvalue(),
                        file_name="Laporan_Master_QRIS.xlsx",
                        mime="application/vnd.ms-excel",
                        type="primary",
                    )
                except ModuleNotFoundError:
                    st.error("🚨 Library 'xlsxwriter' belum terinstall!")