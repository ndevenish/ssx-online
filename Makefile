
.PHONY: run-db

run-db: tests/.built_image
	docker stop -t 0 test-ispyb || true
	docker run -d --rm --name test-ispyb test-ispyb

tests/.built_image: tests/ispyb-database/custom.sql tests/ispyb-database/Dockerfile
	docker build -t test-ispyb tests/ispyb-database
	touch tests/.built_image
