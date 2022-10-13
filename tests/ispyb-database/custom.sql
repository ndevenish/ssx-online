INSERT INTO `Detector` (`detectorId`, `detectorType`, `detectorManufacturer`, `detectorModel`, `detectorPixelSizeHorizontal`, `detectorPixelSizeVertical`, `DETECTORMAXRESOLUTION`, `DETECTORMINRESOLUTION`, `detectorSerialNumber`, `detectorDistanceMin`, `detectorDistanceMax`, `trustedPixelValueRangeLower`, `trustedPixelValueRangeUpper`, `sensorThickness`, `overload`, `XGeoCorr`, `YGeoCorr`, `detectorMode`, `density`, `composition`, `numberOfPixelsX`, `numberOfPixelsY`, `detectorRollMin`, `detectorRollMax`, `localName`)
VALUES
	(58, 'Photon Counting', 'Dectris', 'Pilatus3 6M', 172, 172, 0.8, 50, '60-0119', 200, 1500, NULL, NULL, 450, NULL, NULL, NULL, NULL, NULL, NULL, 2463, 2527, NULL, NULL, NULL),
	(94, 'Photon Counting', 'Dectris', 'EIGER2 X CdTe 9M', 75, 75, NULL, NULL, 'E-18-0115', 200, 1500, NULL, NULL, 750, NULL, NULL, NULL, NULL, NULL, NULL, 3108, 3262, NULL, NULL, NULL);


-- Collection of data for setting up a mirror of a known proposal ID, with fake data

INSERT INTO `Person` (`personId`, `laboratoryId`, `siteId`, `personUUID`, `familyName`, `givenName`, `title`, `emailAddress`, `phoneNumber`, `login`, `faxNumber`, `recordTimeStamp`, `cache`, `externalId`)
VALUES
	(1337, NULL, NULL, NULL, 'Scientist', 'A', 'Dr', NULL, NULL, NULL, NULL, '2022-01-01 00:00:00', NULL, NULL);

INSERT INTO `Proposal` (`proposalId`, `personId`, `title`, `proposalCode`, `proposalNumber`, `bltimeStamp`, `state`)
VALUES
	(51981, 1337, 'Fake proposal for testing', 'mx', '24447', '2020-01-01 00:00:00', 'Open');

INSERT INTO `BLSession` (`proposalId`, `startDate`, `endDate`, `beamLineName`, `scheduled`, `beamLineOperator`, `bltimeStamp`, `visit_number`)
VALUES
	(51981, '2022-10-07 12:00:00', '2022-10-07 20:00:00', 'i24', 1, 'Dr I24', '2022-09-01 00:00:00', 95);
