import socket

def discover_onvif():
    WS_DISCOVERY_MESSAGE = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2004/08/discovery" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        '<e:Header>'
        '<w:MessageID>uuid:84ede405-728b-4f0d-933e-009139265f6d</w:MessageID>'
        '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2004:08:discovery</w:To>'
        '<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2004/08/discovery/Probe</w:Action>'
        '</e:Header>'
        '<e:Body>'
        '<d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe>'
        '</e:Body>'
        '</e:Envelope>'
    )

    MULTICAST_ADDR = '239.255.255.250'
    PORT = 3702

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    
    try:
        sock.sendto(WS_DISCOVERY_MESSAGE.encode(), (MULTICAST_ADDR, PORT))
        while True:
            data, addr = sock.recvfrom(65507)
            print(f"Discovered ONVIF device at {addr}")
            print(data.decode())
            print("-" * 20)
    except socket.timeout:
        print("Discovery timed out.")
    finally:
        sock.close()

if __name__ == "__main__":
    discover_onvif()
