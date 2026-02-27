#!/usr/bin/env python3
from langchain_community.llms import Ollama
from advanced_sales_analyzer import (
    comprehensive_sales_analysis, 
    objection_handling_analysis,
    buying_signals_detection, 
    sales_technique_evaluation,
    comparative_analysis
)
from file_reader_optimized import read_sales_call_file, list_sales_call_files
from file_tools import save_note, read_notes
import os

print("="*70)
print("ULTIMATE SALES CALL ANALYZER")
print("="*70)

class UltimateSalesAnalyzer:
    def __init__(self):
        self.llm = Ollama(model="llama3:8b")
        self.analysis_tools = [
            comprehensive_sales_analysis,
            objection_handling_analysis, 
            buying_signals_detection,
            sales_technique_evaluation,
            comparative_analysis
        ]
        self.file_tools = [read_sales_call_file, list_sales_call_files]
        self.utility_tools = [save_note, read_notes]
        
        print("✅ Advanced Sales Analyzer Loaded!")
        print("\n📊 Available Analysis Types:")
        print("1. Comprehensive Analysis - Full call breakdown")
        print("2. Objection Analysis - Deep dive on objections")  
        print("3. Buying Signals - Identify conversion opportunities")
        print("4. Technique Evaluation - Sales skill assessment")
        print("5. Comparative Analysis - Compare multiple calls")
    
    def run_analysis_pipeline(self, filename: str, analysis_type: str = "comprehensive"):
        """Run optimized analysis pipeline on sales call file."""
        print(f"\n🚀 ANALYZING: {filename}")
        print("=" * 60)
        
        # Read the file
        file_content = read_sales_call_file.invoke(filename)
        if "Error" in file_content:
            print(f"❌ {file_content}")
            return
        
        print(f"📖 Loaded transcript: {len(file_content)} characters")
        
        # Run selected analysis
        if analysis_type == "comprehensive":
            result = comprehensive_sales_analysis.invoke(file_content)
        elif analysis_type == "objections":
            result = objection_handling_analysis.invoke(file_content)
        elif analysis_type == "buying_signals":
            result = buying_signals_detection.invoke(file_content)
        elif analysis_type == "technique":
            result = sales_technique_evaluation.invoke(file_content)
        else:
            result = comprehensive_sales_analysis.invoke(file_content)
        
        print(result)
        
        # Save analysis summary
        summary_note = f"Analysis of {filename} - {analysis_type}: Key insights generated"
        save_note.invoke(summary_note)
        
        return result
    
    def run_comparative_analysis(self, file1: str, file2: str):
        """Compare two sales calls."""
        print(f"\n🔄 COMPARING: {file1} vs {file2}")
        print("=" * 60)
        
        content1 = read_sales_call_file.invoke(file1)
        content2 = read_sales_call_file.invoke(file2)
        
        if "Error" in content1 or "Error" in content2:
            print("❌ Error loading files for comparison")
            return
        
        result = comparative_analysis.invoke(f"File1: {content1[:1500]}\n\nFile2: {content2[:1500]}")
        print(result)
    
    def run_interactive(self):
        """Run interactive analysis session."""
        print("\n" + "="*70)
        print("🎮 INTERACTIVE ANALYSIS MODE")
        print("="*70)
        
        while True:
            try:
                print("\nAvailable Commands:")
                print("1. 'list' - Show all sales call files")
                print("2. 'analyze [filename]' - Comprehensive analysis")
                print("3. 'objections [filename]' - Objection analysis") 
                print("4. 'signals [filename]' - Buying signals analysis")
                print("5. 'technique [filename]' - Sales technique evaluation")
                print("6. 'compare [file1] [file2]' - Compare two calls")
                print("7. 'notes' - Show saved insights")
                print("8. 'quit' - Exit analyzer")
                
                user_input = input("\n💡 Command: ").strip().lower()
                
                if user_input in ['quit', 'exit']:
                    print("👋 Closing Ultimate Sales Analyzer...")
                    break
                
                elif user_input == 'list':
                    result = list_sales_call_files.invoke("")
                    print(result)
                
                elif user_input == 'notes':
                    result = read_notes.invoke("")
                    print(result)
                
                elif user_input.startswith('analyze '):
                    filename = user_input.replace('analyze ', '').strip()
                    self.run_analysis_pipeline(filename, "comprehensive")
                
                elif user_input.startswith('objections '):
                    filename = user_input.replace('objections ', '').strip()
                    self.run_analysis_pipeline(filename, "objections")
                
                elif user_input.startswith('signals '):
                    filename = user_input.replace('signals ', '').strip()
                    self.run_analysis_pipeline(filename, "buying_signals")
                
                elif user_input.startswith('technique '):
                    filename = user_input.replace('technique ', '').strip()
                    self.run_analysis_pipeline(filename, "technique")
                
                elif user_input.startswith('compare '):
                    files = user_input.replace('compare ', '').strip().split()
                    if len(files) == 2:
                        self.run_comparative_analysis(files[0], files[1])
                    else:
                        print("❌ Please provide exactly two filenames: compare file1.txt file2.txt")
                
                else:
                    print("❌ Unknown command. Type 'list' to see available files.")
                    
            except KeyboardInterrupt:
                print("\n\n🛑 Analysis session interrupted.")
                break
            except Exception as e:
                print(f"❌ Error: {str(e)}")

def main():
    analyzer = UltimateSalesAnalyzer()
    
    print("\n" + "="*70)
    print("🚀 READY FOR ADVANCED SALES CALL ANALYSIS!")
    print("="*70)
    print("This analyzer provides:")
    print("• Deep conversation insights")
    print("• Objection handling evaluation") 
    print("• Buying signal detection")
    print("• Sales technique assessment")
    print("• Comparative analysis across calls")
    print("="*70)
    
    analyzer.run_interactive()

if __name__ == "__main__":
    main()
