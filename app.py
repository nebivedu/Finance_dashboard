from flask import Flask, render_template, redirect, request, url_for
import sqlite3
import os
import re
from collections import defaultdict, Counter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "finance.db")
CATEGORY_DB_PATH = os.path.join(BASE_DIR, "categories.db")
MONTH_NAMES = [
    "",
    "januar",
    "februar",
    "marec",
    "april",
    "maj",
    "junij",
    "julij",
    "avgust",
    "september",
    "oktober",
    "november",
    "december",
]

app = Flask(__name__)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # da dobimo dict-style dostop
    return conn


def init_category_db():
    conn = sqlite3.connect(CATEGORY_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transaction_categories (
            transaction_id INTEGER PRIMARY KEY,
            category_id INTEGER NOT NULL,
            assigned_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        );
        """
    )
    conn.commit()
    conn.close()


def get_category_db_connection():
    conn = sqlite3.connect(CATEGORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_description(text: str) -> str:
    """
    Normalizes a transaction description so similar-looking rows
    (different casing, punctuation or numbers) can be grouped together.
    """
    if not text:
        return ""
    cleaned = re.sub(r"[\W_]+", " ", text)
    cleaned = re.sub(r"\d+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned.lower()).strip()
    return cleaned


def apply_suggested_categories(finance_conn):
    """
    Applies categories to uncategorized transactions when we can infer a
    match from other transactions with the same normalized description.
    """
    cat_conn = get_category_db_connection()
    assignment_rows = cat_conn.execute(
        """
        SELECT transaction_id, category_id
        FROM transaction_categories
        """
    ).fetchall()
    assigned_map = {row["transaction_id"]: row["category_id"] for row in assignment_rows}

    all_rows = finance_conn.execute(
        "SELECT TransactionID, Description FROM transactions"
    ).fetchall()

    normalized_votes = defaultdict(lambda: defaultdict(int))
    for row in all_rows:
        norm_key = normalize_description(row["Description"])
        assigned_id = assigned_map.get(row["TransactionID"])
        if assigned_id and norm_key:
            normalized_votes[norm_key][assigned_id] += 1

    updates = []
    for row in all_rows:
        tx_id = row["TransactionID"]
        if tx_id in assigned_map:
            continue
        norm_key = normalize_description(row["Description"])
        votes = normalized_votes.get(norm_key)
        if votes:
            category_id = max(votes.items(), key=lambda item: (item[1], -item[0]))[0]
            updates.append((tx_id, category_id))

    if updates:
        cat_conn.executemany(
            """
            INSERT INTO transaction_categories (
                transaction_id,
                category_id,
                assigned_at
            )
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(transaction_id) DO UPDATE SET
                category_id = excluded.category_id,
                assigned_at = CURRENT_TIMESTAMP
            """,
            updates,
        )
        cat_conn.commit()
    cat_conn.close()


init_category_db()


@app.route("/")
def dashboard():
    # Check if database exists
    if not os.path.exists(DB_PATH):
        return render_template(
            "error.html",
            error_title="Database Not Found",
            error_message="The finance database hasn't been created yet. Please run 'python otp_parser.py' first to parse your PDF files and create the database.",
            action_text="Run the parser",
            action_command="python otp_parser.py"
        )

    try:
        conn = get_db_connection()
        # Attach category database so we can join assignments with transactions.
        conn.execute("ATTACH DATABASE ? AS categories_db", (CATEGORY_DB_PATH,))
    except sqlite3.OperationalError as e:
        return render_template(
            "error.html",
            error_title="Database Error",
            error_message=f"Error accessing database: {str(e)}. Please run 'python otp_parser.py' to create/update the database.",
            action_text="Run the parser",
            action_command="python otp_parser.py"
        )

    # 1) povzetek po mesecih (prihodki / odhodki)
    summary_query = """
        SELECT
            Year,
            Month,
            SUM(CASE WHEN Amount > 0 THEN Amount ELSE 0 END) AS Income,
            SUM(CASE WHEN Amount < 0 THEN -Amount ELSE 0 END) AS Expense
        FROM transactions
        GROUP BY Year, Month
        ORDER BY Year, Month;
    """
    summary_rows = conn.execute(summary_query).fetchall()
    monthly_summary = []
    labels = []
    income_data = []
    expense_data = []
    for row in summary_rows:
        income_val = row["Income"] or 0
        expense_val = row["Expense"] or 0
        net_val = income_val - expense_val
        month_name = MONTH_NAMES[row["Month"]] if 0 < row["Month"] < len(MONTH_NAMES) else str(row["Month"])
        monthly_summary.append(
            {
                "Year": row["Year"],
                "Month": row["Month"],
                "MonthName": month_name,
                "Income": income_val,
                "Expense": expense_val,
                "Net": net_val,
            }
        )
        labels.append(f"{month_name.capitalize()} {row['Year']}")
        income_data.append(income_val)
        expense_data.append(expense_val)

    # 2) transakcije po mesecih (za razširitev na dashboardu)
    details_rows = conn.execute(
        """
        SELECT Year, Month, Date, Description, Amount, Balance
        FROM transactions
        ORDER BY DateISO DESC, TransactionID DESC
        """
    ).fetchall()
    monthly_transactions = defaultdict(list)
    for tx in details_rows:
        key = f"{tx['Year']}-{str(tx['Month']).zfill(2)}"
        monthly_transactions[key].append(
            {
                "date": tx["Date"],
                "description": tx["Description"],
                "amount": tx["Amount"] or 0,
                "balance": tx["Balance"] or 0,
            }
        )

    monthly_transactions = dict(monthly_transactions)

    # 3) skupni statistiki
    total_stats_query = """
        SELECT
            SUM(CASE WHEN Amount > 0 THEN Amount ELSE 0 END) AS TotalIncome,
            SUM(CASE WHEN Amount < 0 THEN -Amount ELSE 0 END) AS TotalExpense,
            COUNT(*) AS TotalTransactions
        FROM transactions;
    """
    total_stats = conn.execute(total_stats_query).fetchone()
    overall_net = (total_stats["TotalIncome"] or 0) - (total_stats["TotalExpense"] or 0)

    # 4) izdatki po kategorijah
    category_spending_query = """
        SELECT c.name AS category, SUM(ABS(t.Amount)) AS total
        FROM transactions t
        JOIN categories_db.transaction_categories tc ON t.TransactionID = tc.transaction_id
        JOIN categories_db.categories c ON tc.category_id = c.id
        WHERE t.Amount < 0
        GROUP BY c.id, c.name
        ORDER BY total DESC;
    """
    category_spending = conn.execute(category_spending_query).fetchall()

    # 5) prihodki po kategorijah
    category_income_query = """
        SELECT c.name AS category, SUM(t.Amount) AS total
        FROM transactions t
        JOIN categories_db.transaction_categories tc ON t.TransactionID = tc.transaction_id
        JOIN categories_db.categories c ON tc.category_id = c.id
        WHERE t.Amount > 0
        GROUP BY c.id, c.name
        ORDER BY total DESC;
    """
    category_income = conn.execute(category_income_query).fetchall()

    # 6) nekategorizirane transakcije
    uncategorized_query = """
        SELECT COUNT(*) AS count, SUM(ABS(Amount)) AS total
        FROM transactions
        WHERE TransactionID NOT IN (
            SELECT transaction_id FROM categories_db.transaction_categories
        )
        AND Amount < 0;
    """
    uncategorized = conn.execute(uncategorized_query).fetchone()

    conn.execute("DETACH DATABASE categories_db")
    conn.close()

    # pripravimo podatke za pie chart (izdatki po kategorijah)
    spending_categories = [row["category"] for row in category_spending]
    spending_amounts = [row["total"] for row in category_spending]

    # pripravimo podatke za pie chart (prihodki po kategorijah)
    income_categories = [row["category"] for row in category_income]
    income_amounts = [row["total"] for row in category_income]

    return render_template(
        "dashboard.html",
        labels=labels,
        income_data=income_data,
        expense_data=expense_data,
        summary_rows=monthly_summary,
        total_income=total_stats["TotalIncome"] or 0,
        total_expense=total_stats["TotalExpense"] or 0,
        total_transactions=total_stats["TotalTransactions"] or 0,
        overall_net=overall_net,
        spending_categories=spending_categories,
        spending_amounts=spending_amounts,
        income_categories=income_categories,
        income_amounts=income_amounts,
        uncategorized_count=uncategorized["count"] or 0,
        uncategorized_total=uncategorized["total"] or 0,
        monthly_transactions=monthly_transactions,
    )


@app.route("/transactions", methods=["GET", "POST"])
def transactions_view():
    finance_conn = get_db_connection()
    # Attach category DB so we can filter/query against assignments.
    finance_conn.execute("ATTACH DATABASE ? AS categories_db", (CATEGORY_DB_PATH,))

    def read_unassigned(source, default_value="1"):
        values = source.getlist("unassigned_only")
        choice = values[-1].strip() if values else default_value
        return choice if choice in ("0", "1") else default_value

    selected_year = request.args.get("year", "").strip()
    selected_month = request.args.get("month", "").strip()
    unassigned_only = read_unassigned(request.args, "1")

    if request.method == "POST":
        tx_id = request.form.get("transaction_id")
        category_id = request.form.get("category_id")
        filter_year = request.form.get("filter_year") or ""
        filter_month = request.form.get("filter_month") or ""
        description_keyword = (request.form.get("description_keyword") or "").strip()
        bulk_action = request.form.get("bulk_action")
        apply_all = request.form.get("apply_all") == "1"
        apply_similar = request.form.get("apply_similar") == "1"
        unassigned_only = read_unassigned(request.form, unassigned_only)

        if filter_year:
            selected_year = filter_year
        if filter_month:
            selected_month = filter_month

        if bulk_action == "apply_suggestions":
            apply_suggested_categories(finance_conn)
            query_params = {}
            if selected_year:
                query_params["year"] = selected_year
            if selected_month:
                query_params["month"] = selected_month
            if unassigned_only:
                query_params["unassigned_only"] = unassigned_only
            finance_conn.close()
            return redirect(url_for("transactions_view", **query_params))

        target_ids: list[str] = []

        if bulk_action == "keyword" and description_keyword:
            pattern = f"%{description_keyword.lower()}%"
            matched_rows = finance_conn.execute(
                """
                SELECT TransactionID FROM transactions
                WHERE LOWER(Description) LIKE ?
                """,
                (pattern,),
            ).fetchall()
            target_ids = [row["TransactionID"] for row in matched_rows]
        elif tx_id:
            target_set = set()
            base_desc_row = finance_conn.execute(
                "SELECT Description FROM transactions WHERE TransactionID = ?",
                (tx_id,),
            ).fetchone()
            base_description = base_desc_row["Description"] if base_desc_row else ""

            if apply_all and base_description:
                same_rows = finance_conn.execute(
                    "SELECT TransactionID FROM transactions WHERE Description = ?",
                    (base_description,),
                ).fetchall()
                target_set.update(row["TransactionID"] for row in same_rows)

            if apply_similar and base_description:
                normalized = normalize_description(base_description)
                if normalized:
                    all_rows = finance_conn.execute(
                        "SELECT TransactionID, Description FROM transactions"
                    ).fetchall()
                    for row in all_rows:
                        if normalize_description(row["Description"]) == normalized:
                            target_set.add(row["TransactionID"])

            if not target_set and tx_id:
                target_set.add(tx_id)

            target_ids = list(target_set)

        if target_ids:
            conn = get_category_db_connection()
            if not category_id:
                placeholders = ",".join("?" for _ in target_ids)
                conn.execute(
                    f"DELETE FROM transaction_categories WHERE transaction_id IN ({placeholders})",
                    target_ids,
                )
            else:
                conn.executemany(
                    """
                    INSERT INTO transaction_categories (
                        transaction_id,
                        category_id,
                        assigned_at
                    )
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(transaction_id) DO UPDATE SET
                        category_id = excluded.category_id,
                        assigned_at = CURRENT_TIMESTAMP
                    """,
                    [(tid, category_id) for tid in target_ids],
                )
            conn.commit()
            conn.close()

        query_params = {}
        if selected_year:
            query_params["year"] = selected_year
        if selected_month:
            query_params["month"] = selected_month
        if unassigned_only:
            query_params["unassigned_only"] = unassigned_only
        finance_conn.close()
        return redirect(url_for("transactions_view", **query_params))

    base_query = """
        SELECT TransactionID, Date, Description, Amount, Balance, Year, Month
        FROM transactions
    """
    filters = []
    params = []
    if selected_year:
        try:
            filters.append("Year = ?")
            params.append(int(selected_year))
        except ValueError:
            selected_year = ""
    if selected_month:
        try:
            filters.append("Month = ?")
            params.append(int(selected_month))
        except ValueError:
            selected_month = ""
    if unassigned_only == "1":
        filters.append(
            """
            TransactionID NOT IN (
                SELECT transaction_id FROM categories_db.transaction_categories
            )
            """
        )
    if filters:
        base_query += " WHERE " + " AND ".join(filters)
    base_query += " ORDER BY DateISO DESC, TransactionID DESC"

    transactions_rows = finance_conn.execute(base_query, params).fetchall()

    year_options = [
        row["Year"]
        for row in finance_conn.execute(
            "SELECT DISTINCT Year FROM transactions ORDER BY Year DESC"
        ).fetchall()
    ]
    month_options = [
        {
            "value": row["Month"],
            "name": MONTH_NAMES[row["Month"]] if 0 < row["Month"] < len(MONTH_NAMES) else str(row["Month"]),
        }
        for row in finance_conn.execute(
            "SELECT DISTINCT Month FROM transactions ORDER BY Month"
        ).fetchall()
    ]
    all_transactions_rows = finance_conn.execute(
        "SELECT TransactionID, Description FROM transactions"
    ).fetchall()
    finance_conn.execute("DETACH DATABASE categories_db")
    finance_conn.close()

    cat_conn = get_category_db_connection()
    categories = cat_conn.execute(
        "SELECT id, name FROM categories ORDER BY name ASC"
    ).fetchall()

    assignment_rows = cat_conn.execute(
        """
        SELECT tc.transaction_id, tc.category_id, c.name AS category_name
        FROM transaction_categories tc
        LEFT JOIN categories c ON c.id = tc.category_id
        """
    ).fetchall()
    cat_conn.close()
    assignments = {
        row["transaction_id"]: {"id": row["category_id"], "name": row["category_name"]}
        for row in assignment_rows
    }
    category_lookup = {row["id"]: row["name"] for row in categories}

    normalized_votes = defaultdict(lambda: defaultdict(int))
    normalized_to_ids = defaultdict(list)
    exact_to_ids = defaultdict(list)
    token_counter = Counter()
    normalized_examples = {}
    for tx in all_transactions_rows:
        norm_key = normalize_description(tx["Description"])
        if norm_key:
            normalized_to_ids[norm_key].append(tx["TransactionID"])
            normalized_examples.setdefault(norm_key, tx["Description"] or "")
            for token in norm_key.split():
                if len(token) >= 3:
                    token_counter[token] += 1
        exact_to_ids[tx["Description"] or ""].append(tx["TransactionID"])
        assigned = assignments.get(tx["TransactionID"])
        if assigned and assigned["id"] and norm_key:
            normalized_votes[norm_key][assigned["id"]] += 1

    keyword_options = []
    for token, count in token_counter.most_common(30):
        if count < 2:
            continue
        keyword_options.append({"value": token, "label": f"{token} ({count}x)"})

    for norm_key, ids in sorted(
        normalized_to_ids.items(), key=lambda item: len(item[1]), reverse=True
    ):
        if len(keyword_options) >= 50:
            break
        if len(ids) < 2:
            continue
        sample = normalized_examples.get(norm_key) or norm_key
        keyword_options.append(
            {"value": sample, "label": f"{sample} ({len(ids)}x podobno)"}
        )

    suggestions = {}
    for tx in all_transactions_rows:
        tx_id = tx["TransactionID"]
        if tx_id in assignments:
            continue
        norm_key = normalize_description(tx["Description"])
        votes = normalized_votes.get(norm_key)
        if votes:
            suggested_id = max(votes.items(), key=lambda item: (item[1], -item[0]))[0]
            suggestions[tx_id] = {
                "category_id": suggested_id,
                "category_name": category_lookup.get(suggested_id),
                "similar_count": max(len(normalized_to_ids.get(norm_key, [])) - 1, 0),
            }

    suggested_total = len(suggestions)
    suggested_in_view = sum(
        1 for tx in transactions_rows if tx["TransactionID"] in suggestions
    )

    transactions = []
    total_income = 0.0
    total_expense = 0.0
    for row in transactions_rows:
        amount = row["Amount"] or 0
        if amount >= 0:
            total_income += amount
        else:
            total_expense += abs(amount)
        assigned = assignments.get(row["TransactionID"])
        norm_key = normalize_description(row["Description"])
        similar_count = max(len(normalized_to_ids.get(norm_key, [])) - 1, 0) if norm_key else 0
        exact_match_count = max(len(exact_to_ids.get(row["Description"] or "", [])) - 1, 0)
        suggested = suggestions.get(row["TransactionID"])
        transactions.append(
            {
                "id": row["TransactionID"],
                "date": row["Date"],
                "description": row["Description"],
                "amount": amount,
                "balance": row["Balance"],
                "year": row["Year"],
                "month": row["Month"],
                "category_id": assigned["id"] if assigned else None,
                "category_name": assigned["name"] if assigned else None,
                "suggested_category_id": suggested["category_id"] if suggested else None,
                "suggested_category_name": suggested["category_name"] if suggested else None,
                "similar_count": similar_count,
                "exact_match_count": exact_match_count,
            }
        )

    return render_template(
        "transactions.html",
        transactions=transactions,
        categories=categories,
        year_options=year_options,
        month_options=month_options,
        selected_year=selected_year,
        selected_month=selected_month,
        total_income=total_income,
        total_expense=total_expense,
        suggested_total=suggested_total,
        suggested_in_view=suggested_in_view,
        unassigned_only=unassigned_only,
        keyword_options=keyword_options,
    )


@app.route("/categories", methods=["GET", "POST"])
def categories_view():
    conn = get_category_db_connection()
    message = None
    error = None

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip() or None

        if not name:
            error = "Category name is required."
        else:
            try:
                conn.execute(
                    "INSERT INTO categories (name, description) VALUES (?, ?)",
                    (name, description),
                )
                conn.commit()
                message = f"Category '{name}' added."
            except sqlite3.IntegrityError:
                error = "Category with this name already exists."

    categories = conn.execute(
        """
        SELECT c.id, c.name, c.description,
               COUNT(tc.transaction_id) AS assignment_count
        FROM categories c
        LEFT JOIN transaction_categories tc ON tc.category_id = c.id
        GROUP BY c.id
        ORDER BY c.name ASC
        """
    ).fetchall()
    conn.close()

    return render_template(
        "categories.html",
        categories=categories,
        message=message,
        error=error,
    )


if __name__ == "__main__":
    app.run(debug=True)
