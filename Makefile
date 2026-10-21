.PHONY: help init up down reset logs attack forever demo lan

SHELL := /bin/bash
COMPOSE := docker compose
ARGS ?=

help:
	@echo "LoadForge · pruebas de carga y resiliencia"
	@echo
	@echo "  make up        construye y levanta la victima (puerto 8000, todas las interfaces)"
	@echo "  make down      detiene todo"
	@echo "  make reset     borra contenedores y red"
	@echo "  make logs      logs de la victima"
	@echo
	@echo "  make attack ARGS=\"--dns target --port 8000 --requests 100 --every 1\""
	@echo "  make forever N=3 REQUESTS=100 EVERY=1     ataque infinito (Ctrl+C)"
	@echo "  make attack ARGS=\"--dns dominio.lab --port 8000 --dns-server 192.168.1.10 --lan\""
	@echo "  make attack ARGS=\"--dns dominio.lab --port 8000 --resolve dominio.lab=172.28.0.2\""
	@echo "  make demo      levanta la victima y lanza 3 contenedores infinitos"
	@echo "  make lan DNS=mi-host.local PORT=80 N=2    contra un host de tu LAN"
	@echo
	@echo "  parametros: --requests N peticiones cada --every S segundos"

init:
	@test -f .env || true

up:
	$(COMPOSE) up -d --build target
	@echo
	@echo "victima: http://$$(hostname -I 2>/dev/null | cut -d' ' -f1):8000"

down:
	$(COMPOSE) --profile attack down --remove-orphans

reset:
	$(COMPOSE) --profile attack down -v --remove-orphans

logs:
	$(COMPOSE) logs -f --tail=50 target

attack:
	@bash scripts/attack.sh $(ARGS)

forever:
	@bash scripts/attack.sh --forever \
		--dns $(or $(DNS),target) --port $(or $(PORT),8000) \
		--requests $(or $(REQUESTS),100) --every $(or $(EVERY),1) \
		--containers $(or $(N),3) $(EXTRA)

demo: up
	@echo
	@echo "### 3 contenedores · 100 peticiones cada 1s · infinito ###"
	@bash scripts/attack.sh --forever --dns target --port 8000 \
		--requests 100 --every 1 --containers 3

lan:
	@bash scripts/attack.sh --lan --forever \
		--dns $(or $(DNS),target) --port $(or $(PORT),80) \
		--requests $(or $(REQUESTS),50) --every $(or $(EVERY),1) \
		--containers $(or $(N),1)
