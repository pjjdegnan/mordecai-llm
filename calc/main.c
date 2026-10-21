/*
 * llm.tns - chat with a local LLM on the laptop over the TI-Nspire USB link.
 *
 * The laptop runs pc/bridge.py, which hosts a NavNet service (SERVICE_ID)
 * through TI's navnet.dll. We enumerate link nodes, connect to that service,
 * and exchange framed messages: 4-byte little-endian length, then payload,
 * split into packets of at most PKT_MAX bytes.
 */
#include <os.h>
#include <stdarg.h>
#include <nspireio/nspireio.h>

#define SERVICE_ID 0x5001
#define PROMPT_MAX 400
#define REPLY_MAX 8192
#define POLL_MS 250
/* NavNet's packet payload limit. TI_NN_GetConnMaxPktSize doesn't exist on
 * CX II OS 6.x (calling it freezes the calculator), so it's fixed here. */
#define PKT_MAX 254
#define REPLY_TIMEOUT_MS (5 * 60 * 1000)

static nio_console csl;
static nn_ch_t ch = NULL;

/* NavNet calls return negative values on error (0 or 1 on success). */
#define NN_OK(r) ((int16_t)(r) >= 0)

static void status(const char *msg)
{
	nio_color(&csl, NIO_COLOR_WHITE, NIO_COLOR_GRAY);
	nio_printf("%s\n", msg);
	nio_color(&csl, NIO_COLOR_WHITE, NIO_COLOR_BLACK);
}

/* Connection details, shown only if connecting fails. */
static char diag[512];
static int diag_len;

static void diagf(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	int n = vsnprintf(diag + diag_len, sizeof(diag) - diag_len, fmt, ap);
	va_end(ap);
	if (n > 0)
		diag_len = diag_len + n < (int)sizeof(diag) ? diag_len + n : (int)sizeof(diag) - 1;
}

/* Handshake: we send HELLO, the bridge echoes it back. Only a channel that
 * answers within HELLO_TIMEOUT_MS is used for chat. */
#define HELLO "LLM1"
#define HELLO_TIMEOUT_MS 3000

static bool link_connect(void)
{
	nn_oh_t oh = TI_NN_CreateOperationHandle();
	nn_nh_t nh = NULL, seen[8];
	int n = 0, i;
	if (!oh)
		return false;
	int16_t r = TI_NN_NodeEnumInit(oh);
	diagf("enum init: %d\n", r);
	if (NN_OK(r)) {
		/* NodeEnumNext may keep returning the last node, so dedupe. */
		while (n < 8 && NN_OK(TI_NN_NodeEnumNext(oh, &nh)) && nh) {
			for (i = 0; i < n && seen[i] != nh; i++)
				;
			if (i < n)
				break;
			seen[n++] = nh;
		}
		TI_NN_NodeEnumDone(oh);
	}
	TI_NN_DestroyOperationHandle(oh);
	diagf("nodes: %d\n", n);

	for (i = 0; i < n && !ch; i++) {
		nn_ch_t c = NULL;
		char buf[16];
		uint32_t got = 0;
		r = TI_NN_Connect(seen[i], SERVICE_ID, &c);
		diagf("node %d: connect %d", i, r);
		if (!NN_OK(r) || !c) {
			diagf("\n");
			continue;
		}
		/* The bridge only learns about a connection when data arrives, so we
		 * speak first and it answers. */
		r = TI_NN_Write(c, HELLO, 4);
		diagf(", send %d", r);
		r = TI_NN_Read(c, HELLO_TIMEOUT_MS, buf, sizeof(buf), (uint32_t)&got);
		diagf(", reply %d (%lu bytes)\n", (int16_t)r, (unsigned long)got);
		if (NN_OK(r) && got >= 4 && !memcmp(buf, HELLO, 4))
			ch = c;
		else
			TI_NN_Disconnect(c);
	}
	return ch != NULL;
}

static void link_disconnect(void)
{
	if (ch)
		TI_NN_Disconnect(ch);
	ch = NULL;
}

static bool send_msg(const char *payload, uint32_t len)
{
	static uint8_t buf[4 + PROMPT_MAX];
	uint32_t step = PKT_MAX;
	uint32_t total = 4 + len, off;
	buf[0] = len & 0xff;
	buf[1] = (len >> 8) & 0xff;
	buf[2] = (len >> 16) & 0xff;
	buf[3] = (len >> 24) & 0xff;
	memcpy(buf + 4, payload, len);
	for (off = 0; off < total; off += step) {
		uint32_t n = total - off < step ? total - off : step;
		if (!NN_OK(TI_NN_Write(ch, buf + off, n)))
			return false;
	}
	return true;
}

/* Returns reply length, or -1 on timeout / cancel (ESC). */
static int recv_msg(char *out, uint32_t out_size)
{
	static uint8_t pkt[1024];
	uint32_t have = 0, want = 0, waited = 0;
	uint8_t hdr[4];
	while (waited < REPLY_TIMEOUT_MS) {
		uint32_t got = 0, i;
		if (isKeyPressed(KEY_NSPIRE_ESC))
			return -1;
		if (!NN_OK(TI_NN_Read(ch, POLL_MS, pkt, sizeof(pkt), (uint32_t)&got)) || !got) {
			waited += POLL_MS;
			continue;
		}
		for (i = 0; i < got; i++, have++) {
			if (have < 4) {
				hdr[have] = pkt[i];
				if (have == 3)
					want = hdr[0] | hdr[1] << 8 | hdr[2] << 16 | (uint32_t)hdr[3] << 24;
			} else if (have - 4 < out_size - 1) {
				out[have - 4] = pkt[i];
			}
		}
		if (have >= 4 && have - 4 >= want) {
			uint32_t n = want < out_size - 1 ? want : out_size - 1;
			out[n] = '\0';
			return n;
		}
	}
	return -1;
}

/* Word-wrapped output that pauses every screenful. */
static void print_paged(const char *s)
{
	const int cols = NIO_MAX_COLS, page = NIO_MAX_ROWS - 2;
	int lines = 0;
	while (*s) {
		int len = 0, brk = -1;
		while (s[len] && s[len] != '\n' && len < cols) {
			if (s[len] == ' ')
				brk = len;
			len++;
		}
		if (s[len] && s[len] != '\n' && s[len] != ' ' && brk > 0)
			len = brk;
		nio_printf("%.*s\n", len, s);
		s += len;
		if (*s == '\n' || *s == ' ')
			s++;
		if (++lines >= page && *s) {
			status("-- more (any key) --");
			wait_no_key_pressed();
			wait_key_pressed();
			wait_no_key_pressed();
			lines = 0;
		}
	}
}

int main(void)
{
	static char prompt[PROMPT_MAX + 1];
	static char reply[REPLY_MAX];

	nio_init(&csl, NIO_MAX_COLS, NIO_MAX_ROWS, 0, 0, NIO_COLOR_WHITE, NIO_COLOR_BLACK, true);
	nio_set_default(&csl);
	nio_color(&csl, NIO_COLOR_WHITE, NIO_COLOR_BLACK);
	status(" Nspire LLM  -  ESC quits, '/new' resets chat ");

	status("Connecting to laptop...");
	if (!link_connect()) {
		nio_printf("%s\nNo bridge found.\n"
			   "1. Quit (any key), stay on the home screen\n"
			   "2. On the laptop: python pc\\bridge.py\n"
			   "3. Wait for 'calculator link up', then reopen\n\n"
			   "Press any key.", diag);
		wait_no_key_pressed();
		wait_key_pressed();
		wait_no_key_pressed();
		nio_free(&csl);
		return 0;
	}
	status("Connected.");

	while (1) {
		nio_color(&csl, NIO_COLOR_WHITE, NIO_COLOR_BLACK);
		nio_printf("\n> ");
		wait_no_key_pressed();
		if (!nio_getsn(prompt, sizeof(prompt)))
			break; /* ESC */
		if (!prompt[0])
			continue;
		if (!send_msg(prompt, strlen(prompt))) {
			status("Send failed - is bridge.py running?");
			continue;
		}
		status("thinking... (ESC cancels)");
		int n = recv_msg(reply, sizeof(reply));
		if (n < 0) {
			status("No reply (timeout or cancelled).");
			wait_no_key_pressed();
			continue;
		}
		nio_color(&csl, NIO_COLOR_WHITE, NIO_COLOR_BLUE);
		print_paged(reply);
	}

	link_disconnect();
	wait_no_key_pressed();
	nio_free(&csl);
	return 0;
}
