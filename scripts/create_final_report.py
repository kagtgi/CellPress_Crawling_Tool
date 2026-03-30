import argparse
import os
import pandas as pd

def main():
    parser = argparse.ArgumentParser(description="Refine the initial report")
    parser.add_argument("--input", type=str, help="Input report")
    parser.add_argument("--output", type=str, help="Output directory")
    
    args = parser.parse_args()

    if not args.input:
        print("Please provide an input report with --input")
        return
    
    if not args.output:
        print("Please provide an output directory with --output")
        return
    
    if not os.path.exists(args.output):
        os.makedirs(args.output, exist_ok=True)

    init_report = pd.read_csv(args.input)

    data = {
        "pmid": init_report["pmid"],
        "title": init_report["title"],
        "abstract": init_report["abstract"],
        "url": init_report["doi"].map(lambda x: f"https://doi.org/{x}" if x else None),
        "pmc_id": init_report["pmc_id"],
        "interest?": [None] * len(init_report),
        "open_access": init_report["open_access"],
        "public_access": init_report["public_access"],
        "categories": init_report["categories"] if "categories" in init_report.columns else [None] * len(init_report)
    }

    final_report = pd.DataFrame(data)
    final_report.to_csv(os.path.join(args.output, args.input.split("/")[-1]), index=False)

if __name__ == "__main__":
    main()
