"""Resolve a request's subject across current and historical identifiers."""


def resolve_identity(request, identities, audit_rows):
    cutoff = request["audit_at"]
    email = request["email"].casefold()
    by_user = {row["user_id"]: row for row in identities}
    history = {}
    for row in audit_rows:
        if row["event_time"] <= cutoff:
            history.setdefault(row["user_id"], []).append(row)
    candidates = []
    for user_id, row in by_user.items():
        values = {str(row[k]).casefold() for k in ("user_id", "name", "email", "phone", "email_hash") if row.get(k)}
        for change in history.get(user_id, []):
            values.update(str(change[k]).casefold() for k in ("old_value", "new_value") if change.get(k))
        if email in values:
            candidates.append((user_id, values))
    if len(candidates) != 1:
        raise ValueError("request email does not resolve to exactly one subject")
    user_id, values = candidates[0]
    return {"user_id": user_id, "values": values, "email": email, "name": by_user[user_id]["name"]}
