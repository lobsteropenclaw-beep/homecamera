import socket

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_MX = 1
SSDP_ST = "ssdp:all"

ssdpRequest = "M-SEARCH * HTTP/1.1\r\n" + \
                "HOST: %s:%d\r\n" % (SSDP_ADDR, SSDP_PORT) + \
                "MAN: \"ssdp:discover\"\r\n" + \
                "MX: %d\r\n" % SSDP_MX + \
                "ST: %s\r\n" % SSDP_ST + "\r\n"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(5)
sock.sendto(ssdpRequest.encode('utf-8'), (SSDP_ADDR, SSDP_PORT))

try:
    while True:
        data, addr = sock.recvfrom(1024)
        print(f"From {addr}:")
        print(data.decode('utf-8', errors='ignore'))
        print("-" * 20)
except socket.timeout:
    pass
