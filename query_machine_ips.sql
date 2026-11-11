-- Mapping unit onboard: OID, nama unit, IP Radio (WGB), IP Display, IP PLE.
-- Dipakai app.py dan collect_ping.py (MACHINE_SOURCE=sql). Boleh diedit tanpa mengubah Python.
-- Kolom WAJIB: OID, SN Unit, IP WGB Radio, IP Display, IP PLE. Kolom opsional: IP GPS (dipantau sebagai tipe G).
-- Catatan: kolom [OID] ditambahkan ke query awal karena dashboard mencocokkan data delay berdasarkan OID.
WITH IP_DATA AS
(
    SELECT
        mc.[parentOID],
        mr.[name] AS [SN Unit],
        LEFT(
            REPLACE(mc.[communications], 'tmac://', ''),
            CHARINDEX(':', REPLACE(mc.[communications], 'tmac://', '') + ':') - 1
        ) AS [IP],
        CASE
            WHEN mc.[interfaceName] = 'Assignment' THEN 'IP Display'
            WHEN mc.[interfaceName] = 'Health' THEN 'IP PLE'
        END AS [Reason]
    FROM [mshist_rep].[dbo].[VM_MACHINE_COMMUNICATIONS] mc
    LEFT JOIN [mshist_rep].[dbo].[V_MACHINE_REFERENCE] mr
        ON mc.[parentOID] = mr.[OID]
    WHERE mc.[interfaceName] IN ('Assignment', 'Health')
)
SELECT
    [parentOID] AS [OID],
    [SN Unit],
    MAX(CASE WHEN [Reason] = 'IP Display' THEN CONCAT(
        PARSENAME([IP],4),'.',PARSENAME([IP],3),'.',PARSENAME([IP],2),'.',
        CAST(PARSENAME([IP],1) AS INT)-1) END) AS [IP WGB Radio],
    MAX(CASE WHEN [Reason] = 'IP Display' THEN [IP] END) AS [IP Display],
    MAX(CASE WHEN [Reason] = 'IP PLE' THEN [IP] END) AS [IP PLE]
FROM IP_DATA
WHERE [SN Unit] IS NOT NULL
GROUP BY [parentOID], [SN Unit]
ORDER BY [SN Unit]
