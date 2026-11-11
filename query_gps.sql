/* QUERY GPS ONB (revisi dari user: PositionReport terakhir tiap unit; SATELLITES NULL = tidak ada data GPS = lampu G merah).
   Isi query tidak diubah, hanya dua tanggal tetap diganti token yang diisi sistem tiap menit:
     __START_WITA__ = sekarang (WITA) dikurangi GPS_QUERY_WINDOW_MINUTES menit (bawaan 1)
     __END_WITA__   = sekarang (WITA)
   Unit tanpa PositionReport pada jendela itu tetap tampil dengan SATELLITES NULL.
   Untuk dijalankan manual di SSMS, ganti kedua token dengan tanggal, mis. '2026-11-06 06:00:00'.
   Cek hasil dari terminal: python gps_metric.py */
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

/* =====================================================
   1. PARAMETER FILTER WAKTU (WITA)
   ===================================================== */

DECLARE @StartWITA DATETIME2(0) = '__START_WITA__';
DECLARE @EndWITA   DATETIME2(0) = '__END_WITA__';


/* =====================================================
   2. KONVERSI WITA KE UTC
   ===================================================== */

DECLARE @StartUTC DATETIME2(0) =
    DATEADD(HOUR, -8, @StartWITA);

DECLARE @EndUTC DATETIME2(0) =
    DATEADD(HOUR, -8, @EndWITA);


/* =====================================================
   3. DAFTAR SELURUH UNIT MINESTAR
   ===================================================== */

;WITH AllMachines AS (
    SELECT DISTINCT
        mr.OID AS MACHINE_OID,
        LTRIM(RTRIM(mr.NAME)) AS MACHINE_NAME

    FROM mshist_rep.dbo.V_MACHINE_REFERENCE mr

    WHERE
        mr.NAME IS NOT NULL
        AND LTRIM(RTRIM(mr.NAME)) <> ''
),


/* =====================================================
   4. GPS POSITION REPORT
      SELURUH UNIT
   ===================================================== */

RankedGPS AS (
    SELECT

        M.MACHINE_OID,
        M.MACHINE_NAME,

        E.CLASS_ID,
        E.EVENTSOURCE,

        DATEADD(
            HOUR,
            8,
            E.TIMESTAMP_UTC
        ) AS TIMESTAMP_WITA,

        E.LOCATION_X,
        E.LOCATION_Y,
        E.LOCATION_Z,

        E.SATELLITES,

        E.NEW_STATE_DESCRIPTION,

        ROW_NUMBER() OVER (
            PARTITION BY M.MACHINE_OID
            ORDER BY E.TIMESTAMP_UTC DESC
        ) AS RN

    FROM AllMachines M

    INNER JOIN mshist_rep.dbo.PRODUCTION_EVENT E
        WITH (NOLOCK)

        ON E.EVENTSOURCE = M.MACHINE_NAME

    WHERE
        -- FILTER PERIODE
        E.TIMESTAMP_UTC >= @StartUTC
        AND E.TIMESTAMP_UTC < @EndUTC

        -- POSITION REPORT ONLY
        AND E.CLASS_ID = 'PositionReport'

        -- FILTER EVENT
        AND E.ENGLISH_DESCRIPTION IS NULL

        -- KOORDINAT VALID
        AND E.LOCATION_X IS NOT NULL
        AND E.LOCATION_Y IS NOT NULL
)


/* =====================================================
   5. HASIL GPS TERAKHIR SETIAP UNIT
      UNIT TANPA DATA TETAP DITAMPILKAN
   ===================================================== */

SELECT

    M.MACHINE_OID,
    M.MACHINE_NAME AS TRUCK,

    G.CLASS_ID,
    G.EVENTSOURCE,

    G.TIMESTAMP_WITA,

    G.LOCATION_X,
    G.LOCATION_Y,
    G.LOCATION_Z,

    G.SATELLITES,

    G.NEW_STATE_DESCRIPTION,

    CASE
        WHEN G.TIMESTAMP_WITA IS NULL THEN 'NO GPS DATA'
        ELSE 'GPS DATA AVAILABLE'
    END AS GPS_DATA_STATUS

FROM AllMachines M

LEFT JOIN RankedGPS G
    ON G.MACHINE_OID = M.MACHINE_OID
    AND G.RN = 1

ORDER BY
    M.MACHINE_NAME ASC

OPTION (RECOMPILE);
