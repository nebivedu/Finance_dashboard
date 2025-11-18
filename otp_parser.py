import os
import glob
import re
import sqlite3
import pdfplumber
import pandas as pd


# Osnovne poti (skripta predvideva, da imaš podmapo "pdf" poleg otp_parser.py)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_FOLDER = os.path.join(BASE_DIR, "pdf")
OUTPUT_EXCEL = os.path.join(BASE_DIR, "otp_transactions.xlsx")

# Regex vzorec, ki ujame vrstice transakcij
# Primer vrstice:
# 11.07.2025 2100901623 SI56023030018888678 915,56  660,91 PRILIV NA RAČUN
# ali:
# 11.07.2025 2100901623 SI56023030018888678  208,54 452,37 NAKUP
# Stolpci v ekstrahiranem besedilu: Datum | ID | Račun | Dobro (credit) | Breme (debit) | Stanje | Opis
# POZOR: V PDF tabeli je naslov Breme | Dobro, ampak ekstrahirano besedilo ima samo dva zneska (pred stanjem),
# kjer je prvi dobro (credit) in drugi breme (debit) - odvisno kateri je prisoten
TX_PATTERN = re.compile(
    r"^(\d{2}\.\d{2}\.\d{4})\s+"                        # datum
    r"(\d{10})\s+"                                      # ID transakcije
    r"(\S+)\s+"                                         # račun / referenca
    r"(\d{1,3}(?:\.\d{3})*,\d{2})?\s*"                  # dobro (credit) - opcijsko
    r"(\d{1,3}(?:\.\d{3})*,\d{2})?\s+"                  # breme (debit) - opcijsko
    r"(-?\d{1,3}(?:\.\d{3})*,\d{2})\s+"                 # stanje po transakciji
    r"(.+)$"                                            # opis
)

START_BALANCE_PATTERN = re.compile(
    r"^EUR\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})\s+"
    r"(-?\d{1,3}(?:\.\d{3})*,\d{2})\s+"
    r"(-?\d{1,3}(?:\.\d{3})*,\d{2})\s+"
    r"(-?\d{1,3}(?:\.\d{3})*,\d{2})"
)

AMOUNT_FIELD_PATTERN = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
ROW_Y_TOLERANCE = 1.0
COLUMN_SPLIT_X = 360.0


def to_float(s: str) -> float:
    """Pretvori znesek v obliki '1.234,56' ali '-208,54' v float."""
    return float(s.replace(".", "").replace(",", "."))


def determine_amount_sign_from_layout(words, txid, amount_str, balance_str):
    """
    Na podlagi x-koordinate številke ugotovimo ali je šlo za bremepis ali dobro.
    Levi stolpec (~320px) so bremepi (negativni), desni (~380px) pa dobro (pozitivni).
    """
    if not words:
        return None

    for word in words:
        if word["text"] == txid:
            row_top = word["top"]
            balance_x = None
            row_words = []
            for candidate in words:
                if abs(candidate["top"] - row_top) <= ROW_Y_TOLERANCE:
                    if candidate["text"] == balance_str and balance_x is None:
                        balance_x = candidate["x0"]
                    if AMOUNT_FIELD_PATTERN.fullmatch(candidate["text"]):
                        row_words.append(candidate)
            if not row_words:
                return None

            # poiščemo številko, ki ustreza amount_str in je levo od stanja
            row_words.sort(key=lambda w: w["x0"])
            for candidate in row_words:
                if candidate["text"] == amount_str:
                    if balance_x is not None and candidate["x0"] >= balance_x:
                        continue
                    return -1 if candidate["x0"] < COLUMN_SPLIT_X else 1
            break
    return None


def parse_single_pdf(path: str) -> pd.DataFrame:
    """Prebere en OTP PDF izpisek in vrne DataFrame z vsemi transakcijami."""
    rows = []
    filename = os.path.basename(path)
    start_balance_set = False
    previous_balance = None

    footer_tokens = (
        "otp banka",
        "d8008",
        "izpis prometa",
        "stran",
        "legenda",
    )

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            page_words = page.extract_words()

            lines = text.splitlines()
            if not start_balance_set:
                for candidate in lines:
                    candidate = candidate.strip()
                    match = START_BALANCE_PATTERN.match(candidate)
                    if match:
                        previous_balance = to_float(match.group(1))
                        start_balance_set = True
                        break

            idx = 0
            while idx < len(lines):
                line = lines[idx]
                m = TX_PATTERN.match(line)
                if not m:
                    idx += 1
                    continue

                date, txid, account, dobro_str, breme_str, balance_str, desc = m.groups()
                desc = desc.strip()

                # Določi znesek glede na stolpec dobro/breme
                if dobro_str:
                    amount_str = dobro_str
                    amount_sign = 1  # pozitiven znesek (priliv)
                elif breme_str:
                    amount_str = breme_str
                    amount_sign = -1  # negativen znesek (odliv)
                else:
                    amount_str = "0,00"
                    amount_sign = 1

                layout_sign = determine_amount_sign_from_layout(
                    page_words, txid, amount_str, balance_str
                )
                if layout_sign is not None:
                    amount_sign = layout_sign

                extra_lines = []

                look_ahead = idx + 1
                while look_ahead < len(lines):
                    nxt = lines[look_ahead].strip()
                    lower = nxt.lower()

                    if not nxt:
                        look_ahead += 1
                        continue
                    if TX_PATTERN.match(nxt):
                        break
                    if any(token in lower for token in footer_tokens):
                        break

                    if nxt != "/":
                        extra_lines.append(nxt)
                    look_ahead += 1

                if extra_lines:
                    desc_parts = [desc] if desc and desc != "." else []
                    desc_parts.extend(extra_lines)
                    desc = " ".join(part.strip() for part in desc_parts).strip()

                balance_value = to_float(balance_str)
                raw_amount_value = to_float(amount_str)
                signed_amount = raw_amount_value * amount_sign
                if previous_balance is not None:
                    diff_amount = round(balance_value - previous_balance, 2)
                    if abs(abs(diff_amount) - raw_amount_value) <= 0.01:
                        signed_amount = diff_amount
                previous_balance = balance_value

                final_sign = 1 if signed_amount >= 0 else -1
                dobro_val = amount_str if final_sign >= 0 else ""
                breme_val = amount_str if final_sign < 0 else ""

                rows.append(
                    {
                        "SourceFile": filename,
                        "Date": date,
                        "TransactionID": txid,
                        "AccountOrRef": account,
                        "DobroRaw": dobro_val,
                        "BremeRaw": breme_val,
                        "AmountRaw": amount_str,
                        "AmountSign": final_sign,
                        "BalanceRaw": balance_str,
                        "Description": desc or "",
                    }
                )
                idx = look_ahead
            # end while

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Izračun numeric vrednosti in dodatnih stolpcev
    df["Amount"] = df["AmountRaw"].map(to_float) * df["AmountSign"]  # znesek s pravilnim predznakom
    df["Balance"] = df["BalanceRaw"].map(to_float)                   # stanje po transakciji
    df["Year"] = df["Date"].str[-4:].astype(int)
    df["Month"] = df["Date"].str[3:5].astype(int)
    df["TransactionID"] = df["TransactionID"].astype(str)

    return df


def build_all_transactions() -> pd.DataFrame:
    """Prebere vse PDF-je iz mape pdf, jih združi z obstoječim Excelom in vrne celotno tabelo."""
    pdf_files = glob.glob(os.path.join(PDF_FOLDER, "*.pdf"))
    if not pdf_files:
        print(f"V mapi {PDF_FOLDER!r} ni nobenega PDF-ja.")
        return pd.DataFrame()

    all_new_dfs = []
    for path in pdf_files:
        print(f"Obdelujem: {os.path.basename(path)}")
        df_pdf = parse_single_pdf(path)
        if not df_pdf.empty:
            all_new_dfs.append(df_pdf)

    if not all_new_dfs:
        print("Nisem našel nobene transakcije v PDF-jih.")
        return pd.DataFrame()

    df_new = pd.concat(all_new_dfs, ignore_index=True)

    # Preberi obstoječi Excel, če obstaja
    if os.path.exists(OUTPUT_EXCEL):
        df_existing = pd.read_excel(OUTPUT_EXCEL)
        if "TransactionID" in df_existing.columns:
            df_existing["TransactionID"] = df_existing["TransactionID"].astype(str)

        # Poravnaj stolpce
        for col in df_new.columns:
            if col not in df_existing.columns:
                df_existing[col] = None
        for col in df_existing.columns:
            if col not in df_new.columns:
                df_new[col] = None

        df_all = pd.concat([df_existing, df_new], ignore_index=True)
    else:
        df_all = df_new

    if "TransactionID" in df_all.columns:
        df_all["TransactionID"] = df_all["TransactionID"].astype(str)

    # Odstrani duplikate po TransactionID
    df_all = df_all.drop_duplicates(subset=["TransactionID"], keep="last")

    # Sortiraj po datumu in ID-ju
    df_all["DateISO"] = pd.to_datetime(df_all["Date"], format="%d.%m.%Y", errors="coerce")
    df_all = df_all.sort_values(["DateISO", "TransactionID"]).reset_index(drop=True)
    
    return df_all


def main():
    print(f"PDF mape: {PDF_FOLDER}")
    print(f"Izhodni Excel: {OUTPUT_EXCEL}")

    df_all = build_all_transactions()
    if df_all.empty:
        print("Ni podatkov za shranit.")
        return

    # 1) Excel backup (če nočeš, lahko to kasneje odstraniš)
    try:
        df_all.to_excel(OUTPUT_EXCEL, index=False)
        print(f"Shranjeno {len(df_all)} transakcij v: {OUTPUT_EXCEL}")
    except PermissionError as exc:
        print(f"Opozorilo: Excel ni bil posodobljen ({exc}). Nadaljujem z zapisom v bazo.")

    # 2) SQLite baza
    db_path = os.path.join(BASE_DIR, "finance.db")
    conn = sqlite3.connect(db_path)

    # shranimo v tabelo "transactions" (vsakič jo prepišemo s celotnim df_all)
    df_all.to_sql("transactions", conn, if_exists="replace", index=False)

    conn.close()
    print(f"Podatki zapisani tudi v SQLite bazo: {db_path}")


if __name__ == "__main__":
    main()
