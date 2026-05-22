// Power Query M — Binalyze AIR asset list for Power BI / Excel  (paginated)
//
// Paste into Power BI Desktop -> Get Data -> Blank Query -> Advanced Editor.
// Replace the three placeholders before running:
//   <AIR_HOST>   e.g. https://your-tenant.binalyze.com
//   <API_TOKEN>  e.g. api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
//   <ORG_ID>     e.g. 0  (default org) or 362, etc.
//
// Fetches ALL assets via a paginated loop — safe for any tenant size.
// PageSize = 1000 keeps each request well under the server-side 10,000 cap
// that causes a 400 Bad Request when exceeded.
//
// Output columns: name, onlineStatus, netInterfaces, lastSeen, createdAt.
// netInterfaces is JSON-stringified for downstream Power BI comparison.

let
    Host      = Text.TrimEnd(Text.Trim("<AIR_HOST>"), "/"),
    Token     = Text.Trim("<API_TOKEN>"),
    OrgIdText = Text.Trim("<ORG_ID>"),
    PageSize  = 1000,
    Url       = Host & "/api/public/assets",

    // Fetch one page; return [Entities, TotalPages]
    GetPage = (page as number) as record =>
        let
            Raw        = Web.Contents(Url, [
                             Query   = [
                                 #"filter[organizationIds]" = OrgIdText,
                                 page     = Text.From(page),
                                 pageSize = Text.From(PageSize)
                             ],
                             Headers = [
                                 #"Authorization" = "Bearer " & Token,
                                 #"Accept"        = "application/json"
                             ]
                         ]),
            Root       = Json.Document(Raw),
            Result     = try Root[result]              otherwise Root,
            Entities   = try Result[entities]          otherwise {},
            TotalPages = try Result[totalPageCount]    otherwise 1
        in
            [ Entities = Entities, TotalPages = TotalPages ],

    // Seed the loop with page 1 so TotalPages is known before iterating
    Page1 = GetPage(1),

    // List.Generate state: [PageNum, Entities, TotalPages]
    // Stops when PageNum exceeds TotalPages or a page comes back empty
    PageLists = List.Generate(
        ()   => [ PageNum    = 1,
                  Entities   = Page1[Entities],
                  TotalPages = Page1[TotalPages] ],
        each [PageNum] <= [TotalPages] and List.Count([Entities]) > 0,
        each let Next = GetPage([PageNum] + 1)
             in  [ PageNum    = [PageNum] + 1,
                   Entities   = Next[Entities],
                   TotalPages = [TotalPages] ],
        each [Entities]
    ),

    AllEntities = List.Combine(PageLists),

    T0 = Table.FromRecords(AllEntities),

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
