"""
cli.py
Command-line interface for the x1025 assistant.

Usage:
    python cli.py                    # interactive REPL
    python cli.py --reingest         # rebuild the vector store from docs
    python cli.py --ask "question"   # one-shot ask

Provider selection: set LLM_PROVIDER (and the matching API key) in .env or in
the environment. See .env.example for the keys each provider needs.
"""
import argparse

# Best-effort .env load — silent if python-dotenv isn't installed.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from x1025.chatbot import X1025Chatbot


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reingest", action="store_true",
                   help="Force re-ingestion of ISM documents into the vector store.")
    p.add_argument("--ask", type=str, default=None,
                   help="One-shot question; exit after answering.")
    args = p.parse_args()

    bot = X1025Chatbot()
    bot.setup(force_reingest=args.reingest)

    if args.ask:
        out = bot.ask(args.ask)
        _print(out)
        return

    print("*" * 80)
    print("x1025 maritime assistant — Layer 1 (SMS) + Layer 2 (operational)")
    print("Type 'exit' or 'quit' to leave.")
    while True:
        try:
            q = input("\nQ: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q.lower() in {"exit", "quit"}:
            break
        if not q:
            continue
        out = bot.ask(q)
        _print(out)


def _print(out: dict) -> None:
    print(f"\n[route: {out['route']}]")
    print(f"A: {out['answer']}")
    if out.get("layer1") and out["layer1"].get("sources"):
        print("\nProcedure sources:")
        for s in out["layer1"]["sources"]:
            print(f"  [{s['index']}] {s['source']} — {s['section']}")
    if out.get("layer2") and out["layer2"].get("tool"):
        print(f"\nOperational tool: {out['layer2']['tool']}({out['layer2'].get('args', {})})")


if __name__ == "__main__":
    main()
