-- ============================================================
-- SoraKids ERP Cash Transaction Import
-- 236 transactions from April 2025 to April 2026
-- Source: Google Sheets Cash Flow tab
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- CASH TRANSACTIONS (236 rows)
-- ------------------------------------------------------------
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA0D8175A6C5822D477D5', '01JSORAKIDS0CMPNY0000001AB', '2025-04-08', 'Chip In Albert', 25000000, 'INFLOW', 'EQUITY_INJECTION', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA78BE0E73667F0E1DFF6', '01JSORAKIDS0CMPNY0000001AB', '2025-04-08', 'Chip In Ferdian', 25000000, 'INFLOW', 'EQUITY_INJECTION', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA799BEC3F4AA3125A3C9', '01JSORAKIDS0CMPNY0000001AB', '2025-04-08', 'Chip In Michael', 25000000, 'INFLOW', 'EQUITY_INJECTION', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORADB3AE3C6C44ABEA94D4', '01JSORAKIDS0CMPNY0000001AB', '2025-04-08', 'Saldo Mengendap 50ribu', 50000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA957B43B95FE43B865A0', '01JSORAKIDS0CMPNY0000001AB', '2025-04-09', 'Biaya Kartu BCA', 20000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA2467CCEC4463B16E6B9', '01JSORAKIDS0CMPNY0000001AB', '2025-04-12', 'Order', 53608101, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA6EC8D416E86FA7FE62D', '01JSORAKIDS0CMPNY0000001AB', '2025-04-18', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA7B06BE39E1B0578EF1A', '01JSORAKIDS0CMPNY0000001AB', '2025-04-30', 'Bunga BCA', 114, 'INFLOW', 'BANK_INTEREST', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA35926318BDD5D2A479A', '01JSORAKIDS0CMPNY0000001AB', '2025-05-07', 'Polymailer', 29300, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAE517997D5714975046D', '01JSORAKIDS0CMPNY0000001AB', '2025-05-07', 'Simcard By-U', 35000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAFE4E3F65D94DEFFE1B6', '01JSORAKIDS0CMPNY0000001AB', '2025-05-07', 'Topup Desty', 504440, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA63FC698C1C403E28A23', '01JSORAKIDS0CMPNY0000001AB', '2025-05-12', 'Shipping Order', 5860000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA387A5C04721EAA432D8', '01JSORAKIDS0CMPNY0000001AB', '2025-05-16', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA0F185931815F4BF4A2D', '01JSORAKIDS0CMPNY0000001AB', '2025-05-19', 'Shopee Ads', 555000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAE84A4591FBCB57DDC3C', '01JSORAKIDS0CMPNY0000001AB', '2025-05-19', 'Reimburse Ferdian FO', 828000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA5E717DE8D9AAA29990F', '01JSORAKIDS0CMPNY0000001AB', '2025-05-19', 'Reimburse Michael FO', 2504000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORACC9DBEDA9D862E9D27B', '01JSORAKIDS0CMPNY0000001AB', '2025-05-19', 'Withdraw Shopee', 3306000, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAB8488F7C8AC250FD40D', '01JSORAKIDS0CMPNY0000001AB', '2025-05-21', 'Refund Supplier Kurang Kirim', 917072, 'INFLOW', 'OTHER_INCOME', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORACC4596EBDEE8CB8D463', '01JSORAKIDS0CMPNY0000001AB', '2025-05-22', 'Shopee Ads', 2220000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA6724CE52DCCCACB9FC1', '01JSORAKIDS0CMPNY0000001AB', '2025-05-23', 'Shipping Order', 3652000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA95421278196A8430ACA', '01JSORAKIDS0CMPNY0000001AB', '2025-05-24', 'Endorse Janice', 375000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA7056143BA5C2885E717', '01JSORAKIDS0CMPNY0000001AB', '2025-05-27', 'Ongkir Endorse', 8000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA767086869772B94B8DE', '01JSORAKIDS0CMPNY0000001AB', '2025-05-27', 'Withdraw Shopee', 3815000, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORACD2AA472E3D9622462C', '01JSORAKIDS0CMPNY0000001AB', '2025-05-30', 'Withdraw Shopee', 2023971, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA51BF08D89DEA5981EA0', '01JSORAKIDS0CMPNY0000001AB', '2025-05-31', 'Withdraw Shopee', 1838920, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA51417B9243B532FB88A', '01JSORAKIDS0CMPNY0000001AB', '2025-06-01', 'Loan Michael', 20000000, 'INFLOW', 'EQUITY_INJECTION', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA9962731BD821866E0C6', '01JSORAKIDS0CMPNY0000001AB', '2025-06-01', 'Loan Ferdian', 20000000, 'INFLOW', 'EQUITY_INJECTION', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA6DF076A75FD54155C79', '01JSORAKIDS0CMPNY0000001AB', '2025-06-01', 'Reimburse Ferdian FO', 1496500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAD9A8483CFAEF514BAFF', '01JSORAKIDS0CMPNY0000001AB', '2025-06-01', 'Shopee Ads', 3300000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAFE2BB72B82945B444DF', '01JSORAKIDS0CMPNY0000001AB', '2025-06-01', 'Bunga BCA', 108, 'INFLOW', 'BANK_INTEREST', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAFB8AD8C25EA4A6F235A', '01JSORAKIDS0CMPNY0000001AB', '2025-06-04', 'Order', 45842468, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA16062D5120AE23E8A56', '01JSORAKIDS0CMPNY0000001AB', '2025-06-04', 'Biaya Admin TF', 2500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA112CD0451F099C69792', '01JSORAKIDS0CMPNY0000001AB', '2025-06-08', 'Reimburse Albert FO', 1055440, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA34868A45FC09C152DF5', '01JSORAKIDS0CMPNY0000001AB', '2025-06-09', 'Polymailer', 39500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA7D49B22738CBA17ECA8', '01JSORAKIDS0CMPNY0000001AB', '2025-06-17', 'Withdraw Shopee', 34690594, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORADF46AB3A13AD584B262', '01JSORAKIDS0CMPNY0000001AB', '2025-06-20', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1E9B8AD5B6FC0BFD1FD', '01JSORAKIDS0CMPNY0000001AB', '2025-06-21', 'Loan Michael', 50000000, 'INFLOW', 'EQUITY_INJECTION', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAA55B7D402BF9A11F447', '01JSORAKIDS0CMPNY0000001AB', '2025-06-21', 'Loan Ferdian', 50000000, 'INFLOW', 'EQUITY_INJECTION', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA57D22610ED840B3A2E9', '01JSORAKIDS0CMPNY0000001AB', '2025-06-23', 'Order', 123078666, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAD3B7CB6643A25F7FC78', '01JSORAKIDS0CMPNY0000001AB', '2025-06-24', 'Refund Supplier Kurang Kirim', 3843700, 'INFLOW', 'OTHER_INCOME', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAC9DD30CC5B529CD3035', '01JSORAKIDS0CMPNY0000001AB', '2025-06-30', 'Withdraw Shopee', 11499043, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAC53FC7E7BF9F2C408EF', '01JSORAKIDS0CMPNY0000001AB', '2025-07-01', 'Bunga BCA', 184, 'INFLOW', 'BANK_INTEREST', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1220A144074292541FC', '01JSORAKIDS0CMPNY0000001AB', '2025-07-09', 'Warung SS', 239800, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA4C0AFEAF0962A72B000', '01JSORAKIDS0CMPNY0000001AB', '2025-07-10', 'Jasa HAKI', 1000000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA986075A7E389D51DEDD', '01JSORAKIDS0CMPNY0000001AB', '2025-07-12', 'Bunga Loan Michael', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA6938F43226D06736678', '01JSORAKIDS0CMPNY0000001AB', '2025-07-12', 'Bunga Loan Ferdian', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAA107073ECC832C448C6', '01JSORAKIDS0CMPNY0000001AB', '2025-07-12', 'Shower', 40700, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA23B58E28D2CC4B9FBD0', '01JSORAKIDS0CMPNY0000001AB', '2025-07-18', 'Bunga Loan Michael', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA49ECEB451C8EE5E00ED', '01JSORAKIDS0CMPNY0000001AB', '2025-07-18', 'Bunga Loan Ferdian', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORADE7DB2162863D63A371', '01JSORAKIDS0CMPNY0000001AB', '2025-07-23', 'Jasa HAKI', 1800000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA94EC90CE76184F45C8A', '01JSORAKIDS0CMPNY0000001AB', '2025-07-28', 'Refill Baygon', 68882, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA93BB7AE6870E5E6879F', '01JSORAKIDS0CMPNY0000001AB', '2025-07-18', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAC006DA4C9D165AD1F7E', '01JSORAKIDS0CMPNY0000001AB', '2025-07-29', 'Sales Shopee', 11762196, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA024BA9F4BF57CE8ABC9', '01JSORAKIDS0CMPNY0000001AB', '2025-07-29', 'Shopee Ads', 56500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA2009B2926AD8140729D', '01JSORAKIDS0CMPNY0000001AB', '2025-07-31', 'Bunga BCA', 210, 'INFLOW', 'BANK_INTEREST', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAD0FD50DCB49C4D0B4A1', '01JSORAKIDS0CMPNY0000001AB', '2025-08-04', 'Shipping Order', 20820000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1DB3F7E6321BF38CB5D', '01JSORAKIDS0CMPNY0000001AB', '2025-08-04', 'Ongkos Angkat Karung Wilopo', 30000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAE00BF10D607404D82F4', '01JSORAKIDS0CMPNY0000001AB', '2025-08-04', 'Polymailer', 349000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORADADA7D5C9227E658538', '01JSORAKIDS0CMPNY0000001AB', '2025-08-13', 'Kertas Thermal', 69500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA755E113C1F00D6301A5', '01JSORAKIDS0CMPNY0000001AB', '2025-08-06', 'Shopee Ads', 1110000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORACDC7C7FF1D06ED93CCF', '01JSORAKIDS0CMPNY0000001AB', '2025-08-14', 'Shopee Ads', 1998000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA7B6B26E96E5AB28AC4F', '01JSORAKIDS0CMPNY0000001AB', '2025-08-14', 'Shipping Order', 1200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA3B9C9179DFD67F88E3D', '01JSORAKIDS0CMPNY0000001AB', '2025-08-15', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA7185BF13969B4F07E58', '01JSORAKIDS0CMPNY0000001AB', '2025-08-25', 'Simcard By-U', 51000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA8CD1C7675852F8FAFF9', '01JSORAKIDS0CMPNY0000001AB', '2025-08-26', 'Shopee Ads', 1110000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA5F6FB2ADA45A0293F77', '01JSORAKIDS0CMPNY0000001AB', '2025-08-28', 'Refund Supplier Kurang Kirim', 7378000, 'INFLOW', 'OTHER_INCOME', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA6252F1D8CF4D4EAF66C', '01JSORAKIDS0CMPNY0000001AB', '2025-08-31', 'Sales Shopee', 21161784, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA86D0FAA3BC4B8A2353F', '01JSORAKIDS0CMPNY0000001AB', '2025-08-31', 'Bunga Loan Michael', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA9966B77FFB1218229C3', '01JSORAKIDS0CMPNY0000001AB', '2025-08-31', 'Bunga Loan Ferdian', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORABD3E7D0F2023A0B9EC3', '01JSORAKIDS0CMPNY0000001AB', '2025-08-31', 'Bunga Loan Michael', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAA94F783C7CF2B126F88', '01JSORAKIDS0CMPNY0000001AB', '2025-08-31', 'Bunga Loan Ferdian', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1111AA2357BD90AEDFC', '01JSORAKIDS0CMPNY0000001AB', '2025-08-31', 'Ongkir Endorse', 16000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAC401CB9AF6D979F9750', '01JSORAKIDS0CMPNY0000001AB', '2025-08-31', 'Reimburse FO', 1936980, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAC8DCA870E92DB5BF5D6', '01JSORAKIDS0CMPNY0000001AB', '2025-08-31', 'Reimburse FO', 192000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA6591E6A6621F044E631', '01JSORAKIDS0CMPNY0000001AB', '2025-09-01', 'Bunga BCA', 124, 'INFLOW', 'BANK_INTEREST', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAB06874F89BA6DCBA3C4', '01JSORAKIDS0CMPNY0000001AB', '2025-09-02', 'Shopee Ads', 1110000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1D3C3F56C8C283BE929', '01JSORAKIDS0CMPNY0000001AB', '2025-09-03', 'Filter', 73600, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAFC2285E1457E09E17BB', '01JSORAKIDS0CMPNY0000001AB', '2025-09-06', 'Shopee Ads', 5550000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA64AF6E65CFE8719033F', '01JSORAKIDS0CMPNY0000001AB', '2025-09-09', 'Container Box', 1392000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA8B61A02951B3C58F616', '01JSORAKIDS0CMPNY0000001AB', '2025-09-19', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA98D153A395A3D2E8AB7', '01JSORAKIDS0CMPNY0000001AB', '2025-09-25', 'Sales Shopee', 35904327, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA035AE4CDB18D8ED0B3F', '01JSORAKIDS0CMPNY0000001AB', '2025-09-25', 'Order', 31711383, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1F9C207A330CEC31911', '01JSORAKIDS0CMPNY0000001AB', '2025-09-30', 'Service AC', 88800, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAADB31C8320ED2F36DEB', '01JSORAKIDS0CMPNY0000001AB', '2025-09-30', 'Filter', 73600, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAAD5668B6EA74E1462C0', '01JSORAKIDS0CMPNY0000001AB', '2025-09-30', 'Bunga Loan Michael', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA15876BF439FBDEB11E1', '01JSORAKIDS0CMPNY0000001AB', '2025-09-30', 'Bunga Loan Ferdian', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAE41A4EA160C3946AF54', '01JSORAKIDS0CMPNY0000001AB', '2025-09-30', 'Bunga Loan Michael', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAEA48C38DC34A217C332', '01JSORAKIDS0CMPNY0000001AB', '2025-09-30', 'Bunga Loan Ferdian', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA0A4E03BA7BA350C73F5', '01JSORAKIDS0CMPNY0000001AB', '2025-09-30', 'Sales Shopee', 4679305, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAACBF492EB906AD87211', '01JSORAKIDS0CMPNY0000001AB', '2025-10-01', 'Bunga BCA', 207, 'INFLOW', 'BANK_INTEREST', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAC95CEE07755F7F019D3', '01JSORAKIDS0CMPNY0000001AB', '2025-10-06', 'Shopee Ads', 3330000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA26C686BE14220E63AEC', '01JSORAKIDS0CMPNY0000001AB', '2025-10-17', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAF6073A43F647D54A6B3', '01JSORAKIDS0CMPNY0000001AB', '2025-10-20', 'Sales Shopee', 25760947, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA557702C58241844C5C6', '01JSORAKIDS0CMPNY0000001AB', '2025-10-20', 'Shopee Ads', 1665000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA97A924C66AA73552637', '01JSORAKIDS0CMPNY0000001AB', '2025-10-23', 'Order', 48848497, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA76D87AFA7FE62C7C0D3', '01JSORAKIDS0CMPNY0000001AB', '2025-10-28', 'Shipping Order', 4048000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA843A889E59D459E9DBB', '01JSORAKIDS0CMPNY0000001AB', '2025-10-31', 'Sales Shopee', 11602508, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAF545F28A49C1229D47E', '01JSORAKIDS0CMPNY0000001AB', '2025-10-31', 'Bunga Loan Michael', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAA6F1E12B04B02400606', '01JSORAKIDS0CMPNY0000001AB', '2025-10-31', 'Bunga Loan Ferdian', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA2DE7D8A9227BD831B1C', '01JSORAKIDS0CMPNY0000001AB', '2025-10-31', 'Bunga Loan Michael', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAD51457518A7C0CC3050', '01JSORAKIDS0CMPNY0000001AB', '2025-10-31', 'Bunga Loan Ferdian', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA2C460F18B61911C976C', '01JSORAKIDS0CMPNY0000001AB', '2025-10-31', 'Reimburse FO', 537000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAE4A2B160C97DD68A736', '01JSORAKIDS0CMPNY0000001AB', '2025-10-31', 'Bunga BCA', 183, 'INFLOW', 'BANK_INTEREST', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA27C2677D29692DDB891', '01JSORAKIDS0CMPNY0000001AB', '2025-11-17', 'Shipping Order', 4780000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORABCCBC3FBC1B6D7AC659', '01JSORAKIDS0CMPNY0000001AB', '2025-11-21', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAF8697E2D0D4A63D050D', '01JSORAKIDS0CMPNY0000001AB', '2025-11-22', 'Shopee Ads', 2997000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAF4367505CC941A64611', '01JSORAKIDS0CMPNY0000001AB', '2025-11-25', 'Sales Shopee', 35062346, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA0B3D0875A96BAE61E39', '01JSORAKIDS0CMPNY0000001AB', '2025-11-25', 'Shipping Order', 10452000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORABD31EEE1CFBB88F189A', '01JSORAKIDS0CMPNY0000001AB', '2025-11-27', 'Shopee Ads', 1443000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA2EBD828B69F7AD3364F', '01JSORAKIDS0CMPNY0000001AB', '2025-11-29', 'Bunga Loan Michael', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA64F5368DC8692D5B2AD', '01JSORAKIDS0CMPNY0000001AB', '2025-11-29', 'Bunga Loan Ferdian', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA51D848E548CC2F7458E', '01JSORAKIDS0CMPNY0000001AB', '2025-11-29', 'Bunga Loan Michael', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA80A1BF109046F6CD1C0', '01JSORAKIDS0CMPNY0000001AB', '2025-11-29', 'Bunga Loan Ferdian', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA783926F846B159D7BA3', '01JSORAKIDS0CMPNY0000001AB', '2025-11-04', 'Shopee Ads', 3163500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1B6A2FE471B47832635', '01JSORAKIDS0CMPNY0000001AB', '2025-11-30', 'Sales Shopee', 8196476, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA4C2A813820939971156', '01JSORAKIDS0CMPNY0000001AB', '2025-11-30', 'Prive Albert', 3000000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAC1314DA391DA9AAD3E0', '01JSORAKIDS0CMPNY0000001AB', '2025-12-01', 'Bunga BCA', 82, 'INFLOW', 'BANK_INTEREST', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA2B9E0AEFF96865DACC1', '01JSORAKIDS0CMPNY0000001AB', '2025-12-02', 'Shopee Ads', 1665000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA912F43DEFF25E687A13', '01JSORAKIDS0CMPNY0000001AB', '2025-12-06', 'Sales Shopee', 12004563, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORADA9DD1ED3B103798BC5', '01JSORAKIDS0CMPNY0000001AB', '2025-12-06', 'Sales Shopee', 3226634, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAB9FED5C138B09CC03FC', '01JSORAKIDS0CMPNY0000001AB', '2025-12-07', 'Order', 42273981, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAC919BCA7C71E32C9F17', '01JSORAKIDS0CMPNY0000001AB', '2025-12-15', 'Sales Manual', 176000, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA6CCF85C21B936775A6C', '01JSORAKIDS0CMPNY0000001AB', '2025-12-19', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1AE276952EFB694A2A6', '01JSORAKIDS0CMPNY0000001AB', '2025-12-22', 'Duplikat Kunci', 150000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA471D1FBAAE65B036A12', '01JSORAKIDS0CMPNY0000001AB', '2025-12-23', 'Lalamove', 322500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA6C4AA6079BC815A409F', '01JSORAKIDS0CMPNY0000001AB', '2025-12-23', 'Sales Shopee', 51136871, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA4A787D39DAD338236BD', '01JSORAKIDS0CMPNY0000001AB', '2025-12-23', 'Shopee Ads', 8880000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA312076D8A01140782AA', '01JSORAKIDS0CMPNY0000001AB', '2025-12-23', 'Shopee Ads', 3330000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA77EDF4B7007A4C41D6A', '01JSORAKIDS0CMPNY0000001AB', '2025-12-26', 'Polymailer', 483800, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAFDB7174281AEA03F54F', '01JSORAKIDS0CMPNY0000001AB', '2025-12-26', 'Order', 34277030, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAA2134E23C5ACCE0E010', '01JSORAKIDS0CMPNY0000001AB', '2025-12-26', 'Tempat Sikat Gigi', 49440, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA9C4B569845C8EEB902E', '01JSORAKIDS0CMPNY0000001AB', '2025-12-26', 'Tong Sampah', 294246, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA4924DA89C933EA2356F', '01JSORAKIDS0CMPNY0000001AB', '2025-12-26', 'Fotokopi', 16000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAA9DF2E26153C564DB33', '01JSORAKIDS0CMPNY0000001AB', '2025-12-27', 'Keripik Kentang', 130000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAACF9EF99DD353829429', '01JSORAKIDS0CMPNY0000001AB', '2025-12-31', 'Sales Shopee', 26988504, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAA8A9236009257C4ACA9', '01JSORAKIDS0CMPNY0000001AB', '2025-12-31', 'Bunga BCA', 83, 'INFLOW', 'BANK_INTEREST', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAC3A977D16B70FEFA5D8', '01JSORAKIDS0CMPNY0000001AB', '2025-12-31', 'Bunga Loan Michael', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1BBEDAF9D9DBE6D067A', '01JSORAKIDS0CMPNY0000001AB', '2025-12-31', 'Bunga Loan Ferdian', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAAE389246B25FE382B04', '01JSORAKIDS0CMPNY0000001AB', '2025-12-31', 'Bunga Loan Michael', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAA1E8B68421BD4EC4C8C', '01JSORAKIDS0CMPNY0000001AB', '2025-12-31', 'Bunga Loan Ferdian', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA49A28B115D68493364B', '01JSORAKIDS0CMPNY0000001AB', '2025-12-31', 'Sales Tiktok', 265020, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA2BCF5F78883343A0556', '01JSORAKIDS0CMPNY0000001AB', '2025-12-31', 'Shopee Ads', 3330000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAD5C03ECF4EA64CE673B', '01JSORAKIDS0CMPNY0000001AB', '2025-12-31', 'Prive Albert', 3000000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAA892DE388695F27273D', '01JSORAKIDS0CMPNY0000001AB', '2026-01-03', 'Shopee Ads', 7770000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAE19D18AACECBF54189F', '01JSORAKIDS0CMPNY0000001AB', '2026-01-03', 'Vacuum Cleaner', 200200, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAAE749BD4943BDBF7DB7', '01JSORAKIDS0CMPNY0000001AB', '2026-01-03', 'Karpet', 93960, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA85D0BB29AE995C20F7E', '01JSORAKIDS0CMPNY0000001AB', '2026-01-07', 'Tangga', 193160, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAFDCF5FE201A96115008', '01JSORAKIDS0CMPNY0000001AB', '2026-01-08', 'Galon', 49200, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1AAA141E1FA2A564A87', '01JSORAKIDS0CMPNY0000001AB', '2026-01-08', 'Topup Desty', 104440, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA55F8DE6A0F129DC8114', '01JSORAKIDS0CMPNY0000001AB', '2026-01-09', 'Cable Organizer', 76900, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA9575F0F525AB7F8B2F9', '01JSORAKIDS0CMPNY0000001AB', '2026-01-09', 'Container Box', 533000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA17E7D07CC9686596D10', '01JSORAKIDS0CMPNY0000001AB', '2026-01-10', 'Rak Galon', 135449, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA3A644412FD6C877DDF3', '01JSORAKIDS0CMPNY0000001AB', '2026-01-10', 'Sabun', 105211, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA6B2334E179BB3A25A18', '01JSORAKIDS0CMPNY0000001AB', '2026-01-13', 'Sales Shopee', 33350870, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA498EF56A61FB083CB30', '01JSORAKIDS0CMPNY0000001AB', '2026-01-13', 'Order', 35496375, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA24564734BAA081BC28E', '01JSORAKIDS0CMPNY0000001AB', '2026-01-16', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA6CF06BA2868FF4680BC', '01JSORAKIDS0CMPNY0000001AB', '2026-01-19', 'Galon', 52000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAE99F2A787255DDE4234', '01JSORAKIDS0CMPNY0000001AB', '2026-01-19', 'Tempat Cuci Piring', 40454, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAE8A365EC5F39DAEED1A', '01JSORAKIDS0CMPNY0000001AB', '2026-01-19', 'Tempat Sabun Cuci Piring', 44041, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA53751B8B3AC5228CD94', '01JSORAKIDS0CMPNY0000001AB', '2026-01-19', 'Teko', 76005, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA244B8C1F26E3C256235', '01JSORAKIDS0CMPNY0000001AB', '2026-01-19', 'Galon', 46200, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA479B8BD148DB53E4887', '01JSORAKIDS0CMPNY0000001AB', '2026-01-20', 'Tongkat Lampu', 42000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA0C5480A4B8CC1AB8A7C', '01JSORAKIDS0CMPNY0000001AB', '2026-01-23', 'Galon', 52000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAF082B25A74D2DB38201', '01JSORAKIDS0CMPNY0000001AB', '2026-01-24', 'Soklin', 33660, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAD1A12C665C8AEFC4F9A', '01JSORAKIDS0CMPNY0000001AB', '2026-01-24', 'Wipol', 63910, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA11ED655BBFC7B352E22', '01JSORAKIDS0CMPNY0000001AB', '2026-01-31', 'Sales Shopee', 19765186, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAEEF71469AE14E19D199', '01JSORAKIDS0CMPNY0000001AB', '2026-01-31', 'Bunga BCA', 99, 'INFLOW', 'BANK_INTEREST', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA91EF2176DE39308235D', '01JSORAKIDS0CMPNY0000001AB', '2026-01-31', 'Bunga Loan Michael', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA67B0E2E7BC0EE6A175A', '01JSORAKIDS0CMPNY0000001AB', '2026-01-31', 'Bunga Loan Ferdian', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAD006D405D913E126E53', '01JSORAKIDS0CMPNY0000001AB', '2026-01-31', 'Bunga Loan Michael', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA31CB599C78C09409841', '01JSORAKIDS0CMPNY0000001AB', '2026-01-31', 'Bunga Loan Ferdian', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAF365217B0E793118B35', '01JSORAKIDS0CMPNY0000001AB', '2026-01-31', 'Prive Albert', 3000000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAEA19E8ACE11281C80AD', '01JSORAKIDS0CMPNY0000001AB', '2026-02-02', 'Refund Container', 247194, 'INFLOW', 'OTHER_INCOME', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA660D64F4C5A18992158', '01JSORAKIDS0CMPNY0000001AB', '2026-02-02', 'Galon', 43000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORADFE4220B6323863E855', '01JSORAKIDS0CMPNY0000001AB', '2026-02-02', 'Shopee Ads', 3330000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA20D863DEF3718C4C350', '01JSORAKIDS0CMPNY0000001AB', '2026-02-03', 'Shipping Order', 9141000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA42C72F2A036707CA13C', '01JSORAKIDS0CMPNY0000001AB', '2026-02-06', 'Galon', 43000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAAE4C48D6C8FE3D9F5C5', '01JSORAKIDS0CMPNY0000001AB', '2026-02-08', 'Shopee Ads', 3108000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA3B8EF0A35B5AC7B3F89', '01JSORAKIDS0CMPNY0000001AB', '2026-02-12', 'Shipping Order', 5555000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA8D177DBED4EB86DD31D', '01JSORAKIDS0CMPNY0000001AB', '2026-02-18', 'Shopee Ads', 3996000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA0C155D4FB314F6AD993', '01JSORAKIDS0CMPNY0000001AB', '2026-02-18', 'Galon', 64500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA483A58D8FD690DEC7D2', '01JSORAKIDS0CMPNY0000001AB', '2026-02-20', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA7B362A4C43B79602B0B', '01JSORAKIDS0CMPNY0000001AB', '2026-02-23', 'Shopee Ads', 1221000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA34F44824B45B2F55347', '01JSORAKIDS0CMPNY0000001AB', '2026-02-28', 'Galon', 43000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAC40647E1A6137A3BE58', '01JSORAKIDS0CMPNY0000001AB', '2026-02-28', 'Plastik Sampah', 51074, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA67D829502BBE786D4D4', '01JSORAKIDS0CMPNY0000001AB', '2026-02-28', 'Sales Shopee', 104278429, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA3550767991433AD2B9C', '01JSORAKIDS0CMPNY0000001AB', '2026-02-28', 'Bunga Loan Michael', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAE0066C96965BCC34DD8', '01JSORAKIDS0CMPNY0000001AB', '2026-02-28', 'Bunga Loan Ferdian', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAAD3B2980411BB82EEC2', '01JSORAKIDS0CMPNY0000001AB', '2026-02-28', 'Bunga Loan Michael', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA5880A30ACB7E9CBAB0C', '01JSORAKIDS0CMPNY0000001AB', '2026-02-28', 'Bunga Loan Ferdian', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORABAEDABF25555A355EB4', '01JSORAKIDS0CMPNY0000001AB', '2026-02-28', 'Shopee Ads', 5550000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA73EAA9A5BDBE6D8A8D9', '01JSORAKIDS0CMPNY0000001AB', '2026-03-03', 'Topup Desty', 104440, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAD4EB4C12543B678FFD0', '01JSORAKIDS0CMPNY0000001AB', '2026-03-04', 'Prive Albert', 3000000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA43D1AB0FA7EA218D6CF', '01JSORAKIDS0CMPNY0000001AB', '2026-03-05', 'Order', 43643701, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAEEF9D995485FD37A7E7', '01JSORAKIDS0CMPNY0000001AB', '2026-03-05', 'Sales Shopee', 11297779, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAD6428B8AB353A3CD89C', '01JSORAKIDS0CMPNY0000001AB', '2026-03-05', 'Order', 53826796, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA07532F95E7F47E3B52C', '01JSORAKIDS0CMPNY0000001AB', '2026-03-09', 'Shopee Ads', 1831500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORACA59DE389210E499060', '01JSORAKIDS0CMPNY0000001AB', '2026-03-11', 'Bidet', 129381, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA9F28804D38EB955D9FC', '01JSORAKIDS0CMPNY0000001AB', '2026-03-12', 'Galon', 64500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA852DE0ED3970F770940', '01JSORAKIDS0CMPNY0000001AB', '2026-03-12', 'Shopee Ads', 3330000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA4E96D5581753AAA89F5', '01JSORAKIDS0CMPNY0000001AB', '2026-03-14', 'Kain Pel', 89393, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAD795449817726C4F2FC', '01JSORAKIDS0CMPNY0000001AB', '2026-03-16', 'Topup Desty', 204440, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA5713D36240E76291223', '01JSORAKIDS0CMPNY0000001AB', '2026-03-20', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAAAD60178A36B2B0998F', '01JSORAKIDS0CMPNY0000001AB', '2026-03-21', 'Tissue', 90700, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAA64806AFD8B62950311', '01JSORAKIDS0CMPNY0000001AB', '2026-03-26', 'Sales Shopee', 72871775, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAF1FF2DCBC8FF9129DD9', '01JSORAKIDS0CMPNY0000001AB', '2026-03-26', 'Shipping Order', 11067000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1C0B0E006A3BD3982CC', '01JSORAKIDS0CMPNY0000001AB', '2026-03-26', 'Alfamart', 16800, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA19B9C3F61FE0DAC8432', '01JSORAKIDS0CMPNY0000001AB', '2026-03-27', 'Galon', 64500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAFF1063B9944278C65A4', '01JSORAKIDS0CMPNY0000001AB', '2026-03-27', 'Prive Albert', 3000000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA8B076193D1E6B7AFFB6', '01JSORAKIDS0CMPNY0000001AB', '2026-03-31', 'Sales Shopee', 7501139, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA85721A8F1AEBB254ECC', '01JSORAKIDS0CMPNY0000001AB', '2026-03-31', 'Sales Tiktok', 596840, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1AD1464CF444C226BAB', '01JSORAKIDS0CMPNY0000001AB', '2026-03-31', 'Bunga Loan Michael', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAC68ED3B3B0B034A39FB', '01JSORAKIDS0CMPNY0000001AB', '2026-03-31', 'Bunga Loan Ferdian', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAEF3FF599CF75AA6A82D', '01JSORAKIDS0CMPNY0000001AB', '2026-03-31', 'Bunga Loan Michael', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA8498991644C0478D36C', '01JSORAKIDS0CMPNY0000001AB', '2026-03-31', 'Bunga Loan Ferdian', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORACFE1AE0A32BCB27F52E', '01JSORAKIDS0CMPNY0000001AB', '2026-04-01', 'Bunga BCA', 215, 'INFLOW', 'BANK_INTEREST', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA23D4D9FD63D2B5144E5', '01JSORAKIDS0CMPNY0000001AB', '2026-04-01', 'Pajak Bunga BCA', 43, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAC9CF23A2569E1AD49E0', '01JSORAKIDS0CMPNY0000001AB', '2026-04-02', 'Shopee Ads', 3330000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA9EDA80AAE54211E48C9', '01JSORAKIDS0CMPNY0000001AB', '2026-04-03', 'Order', 47053233, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAAAF3917D7B1B1B6C8D9', '01JSORAKIDS0CMPNY0000001AB', '2025-04-09', 'Shopee Ads', 1110000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAB16D1B4B5998C92DA82', '01JSORAKIDS0CMPNY0000001AB', '2025-04-17', 'Galon', 43000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1B61A9A2A275BD6159A', '01JSORAKIDS0CMPNY0000001AB', '2025-04-17', 'Galon', 21500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA50D3894712E44EEB745', '01JSORAKIDS0CMPNY0000001AB', '2025-04-17', 'Biaya Admin BCA', 14000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA50E15EA8B822970AD61', '01JSORAKIDS0CMPNY0000001AB', '2025-04-19', 'Shopee Ads', 1110000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA263A6A4111F7FC1C0C8', '01JSORAKIDS0CMPNY0000001AB', '2026-04-22', 'Order', 9725209, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORAF7EAC68E6D130CD6DE9', '01JSORAKIDS0CMPNY0000001AB', '2026-04-25', 'Hogasan (racun semut)', 101688, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA5B17D633F881BE16118', '01JSORAKIDS0CMPNY0000001AB', '2026-04-27', 'Galon', 64500, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA25BAFD2AB77230B90A6', '01JSORAKIDS0CMPNY0000001AB', '2026-04-30', 'Bunga BCA', 145, 'INFLOW', 'BANK_INTEREST', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1501161C84C8E3659D7', '01JSORAKIDS0CMPNY0000001AB', '2026-04-30', 'Pajak Bunga BCA', 29, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA2674B0B41FF618180EA', '01JSORAKIDS0CMPNY0000001AB', '2026-04-30', 'Bunga Loan Michael', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA0ABF1B4071ED4B1D426', '01JSORAKIDS0CMPNY0000001AB', '2026-04-30', 'Bunga Loan Ferdian', 200000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA6511BDC5099794E1E59', '01JSORAKIDS0CMPNY0000001AB', '2026-04-30', 'Bunga Loan Michael', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA3D955D88B6B2C4850FF', '01JSORAKIDS0CMPNY0000001AB', '2026-04-30', 'Bunga Loan Ferdian', 500000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORACCBFD0F2EE084299156', '01JSORAKIDS0CMPNY0000001AB', '2026-04-30', 'Sales Shopee', 28855195, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA1557DBCC9BFBBA356CD', '01JSORAKIDS0CMPNY0000001AB', '2026-04-30', 'Sales Tiktok', 123450, 'INFLOW', 'SALES_SETTLEMENT', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
INSERT INTO finance_cashtransaction (id, company_id, transaction_date, description, amount, transaction_type, category, reference_number, note, cdate, udate)
VALUES ('01JSORA83303BC801F139BD46C', '01JSORAKIDS0CMPNY0000001AB', '2026-04-30', 'Prive Albert', 3000000, 'OUTFLOW', 'OTHER_EXPENSE', '', '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

COMMIT;