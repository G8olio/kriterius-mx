"""Autoriza un paso de Chrome tras comprobar de nuevo el túnel; no hace HTTP.

Requiere una verificación externa reciente y la IP observada en una pestaña de
Chrome. Reutiliza el verificador local del usuario, fuera del paquete del servidor.
El permiso dura 30 segundos, está ligado al paso y se consume una sola vez.
"""
import argparse
import importlib.util
import ipaddress
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path


def comprobar(cliente, verificacion, ip_chrome, paso):
    report = json.loads(Path(verificacion).read_text())
    ahora = datetime.now(timezone.utc)
    fecha = datetime.fromisoformat(report["created_at"])
    if report.get("verified") is not True or fecha.tzinfo is None or not timedelta(0) <= ahora-fecha <= timedelta(minutes=15):
        raise ValueError("Se requiere verificación externa nueva (máximo 15 minutos)")
    if str(ipaddress.IPv4Address(ip_chrome)) != report["public_ipv4"]:
        raise ValueError("La IP de Chrome no coincide con la verificada")
    spec = importlib.util.spec_from_file_location("cliente_vpn_kriterius", cliente)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.unchanged(report["state"])
    return {"verificado": True, "paso": paso, "emitido_utc": ahora.isoformat(),
            "vence_utc": (ahora+timedelta(seconds=30)).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cliente", type=Path, required=True)
    parser.add_argument("--verificacion", type=Path, required=True)
    parser.add_argument("--ip-chrome", required=True)
    parser.add_argument("--paso", required=True)
    parser.add_argument("--salida", type=Path, required=True)
    args = parser.parse_args()
    # Un permiso anterior no sobrevive a una comprobación fallida.
    args.salida.unlink(missing_ok=True)
    try:
        permiso = comprobar(args.cliente, args.verificacion, args.ip_chrome, args.paso)
    except Exception as error:
        parser.exit(2, f"Chrome detenido: {error}\n")
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(permiso, indent=2))
    args.salida.chmod(0o600)
    print(json.dumps(permiso))


if __name__ == "__main__":
    main()
