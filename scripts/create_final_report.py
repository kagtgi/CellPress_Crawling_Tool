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
        "title": init_report["title"],
        "pmid": init_report["pmid"],
        "pmc_id": init_report["pmc_id"],
        "pmc_url": init_report["pmc_url"],
        "pm_url": init_report["url"],
        "url": init_report["doi"].map(lambda x: f"https://doi.org/{x}" if x else None),
        "interest?": [None] * len(init_report)
    }

    final_report = pd.DataFrame(data)
    final_report.to_csv(os.path.join(args.output, args.input.split("/")[-1]), index=False)

if __name__ == "__main__":
    main()
