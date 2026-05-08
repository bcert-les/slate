// Power Query M — Binalyze AIR asset list for Power BI / Excel
//
// Paste this into Power BI Desktop -> Get Data -> Blank Query -> Advanced Editor.
// Replace the three placeholders below before running:
//   <AIR_HOST>     e.g. https://your-tenant.binalyze.com
//   <API_TOKEN>    e.g. api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
//   <ORG_ID>       e.g. 0  (default org) or 362, etc.
//
// Single-shot call to GET /api/public/assets with pageSize=10000 — no pagination
// loop, no separate /export endpoint. Deduplicates by _id and trims to the
// analysis columns:
//   name, onlineStatus, netInterfaces, lastSeen, createdAt.
// netInterfaces is JSON-stringified for downstream comparison.
//
// If your tenant has more than 10,000 assets, raise PageSize accordingly.

let
    Host = Text.TrimEnd(Text.Trim("<AIR_HOST>"), "/"),
    Token = Text.Trim("<API_TOKEN>"),
    OrgIdText = Text.Trim("<ORG_ID>"),
    PageSize = 10000,

    Url = Host & "/api/public/assets",

    Raw = Web.Contents(Url, [
        Query = [
            #"filter[organizationIds]" = OrgIdText,
            page = "1",
            pageSize = Text.From(PageSize)
        ],
        Headers = [
            #"Authorization" = "Bearer " & Token,
            #"Accept" = "application/json"
        ]
    ]),
    Root = Json.Document(Raw),

    Entities =
        let
            wrapped = try Root[result][entities] otherwise null,
            top = if Value.Type(Root) = type list then Root else null
        in
            if wrapped <> null then wrapped
            else if top <> null then top
            else {},

    T0 = Table.FromRecords(Entities),

    T0Deduped =
        if List.Contains(Table.ColumnNames(T0), "_id") then
            Table.Distinct(T0, {"_id"})
        else
            T0,

    T1 = Table.SelectColumns(
        T0Deduped,
        {"name", "onlineStatus", "netInterfaces", "lastSeen", "createdAt"},
        MissingField.Ignore
    ),

    T2 =
        if List.Contains(Table.ColumnNames(T1), "netInterfaces") then
            Table.TransformColumns(
                T1,
                {{
                    "netInterfaces",
                    each
                        if _ = null then
                            null
                        else if Value.Type(_) = type text then
                            _
                        else
                            try Text.FromBinary(Json.FromValue(_)) otherwise Text.From(_),
                    type nullable text
                }}
            )
        else
            T1
in
    T2
