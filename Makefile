
.PHONY: run-db server

nothing:
	@echo "Options:"
	@echo "  run-db:   Build and run a test ispyb database container"
	@echo "  server:   Run the Python FastAPI server part only"

run-db: tests/.built_image test-ispyb.cfg
	docker stop -t 0 test-ispyb || true
	docker run -d --rm --name test-ispyb -p 3306:3306 test-ispyb

tests/.built_image: tests/ispyb-database/custom.sql tests/ispyb-database/Dockerfile
	docker build -t test-ispyb tests/ispyb-database
	touch tests/.built_image

test-ispyb.cfg:
	echo "[ispyb_sqlalchemy]\nusername = root\npassword = \nhost = localhost\nport = 3306\ndatabase = ispyb" > test-ispyb.cfg

server:
	poetry run uvicorn ssx_online.fast:app --reload --port 5000