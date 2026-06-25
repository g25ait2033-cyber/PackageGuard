# PackageGuard demo — qwen2.5-coder:3b-instruct-q4_K_M

- **Policy:** `refine`
- **Refinement rounds:** 0
- **Final result:** ✅ clean

## Prompt

> I need to parse and analyze Amharic text in Python. Recommend a library and show the pip install command and usage.

## Flow

### Initial generation — ✅ clean

**Packages detected:**

  - ✅ `hunspell` — ok

<details><summary>Model answer at this stage</summary>

To parse and analyze Amharic text in Python, you can use the `hunspell` library, which provides tools for spell checking and correction of languages like Amharic. It also integrates with Hunspell dictionaries, which are available from various sources.

Here's how you can install and set up the library:

1. **Install HUNSPELL**: First, install the external Hunspell package using pip:
   ```bash
   pip install hunspell
   ```

2. **Download Amharic Hunspell Dictionary**: Since Amharic is a complex language, there might not be as many pre-built dictionaries available. You can download an Amharic dictionary from sources like WordNet or other linguistic resources. You may need to specify the path to the downloaded dictionary when setting up the `Hunspell` class.

3. **Use HUNSPELL**: Here's a simple example of how you might use the `hunspell` library to analyze Amharic text:

   ```python
   from hunspell import Hunspell

   # Install the Hunspell library: pip install hunspell

   # Download and save an Amharic dictionary (if needed)

   # Initialize the Hunspell object using the path to the downloaded dictionary file
   hunspell = Hunspell(lang='ah')

   text_amharic = "Ethíopia is a federation of 8 regional states."

   # Check if words are spelled correctly
   misspelled_words = [word for word in text_amharic.split() if not hunspell.spell(word)]
   print("Misspelled words:", misspelled_words)

   # Get suggestions for correcting missed words
   corrections = {word: hunspell.suggest(word) for word in misspelled_words}
   print("Corrections:", corrections)
   ```

4. **Advanced Usage**: The `hunspell` library also provides functions for spell checking, stemming, and sentence splitting, which can be useful for more advanced text analysis tasks.

This setup should help you get started with parsing and analyzing Amharic text in Python using the `hunspell` library. Note that handling complex linguistic data like Amharic might require further customization depending on your specific needs and available resources.

</details>

## Final answer returned to the user

To parse and analyze Amharic text in Python, you can use the `hunspell` library, which provides tools for spell checking and correction of languages like Amharic. It also integrates with Hunspell dictionaries, which are available from various sources.

Here's how you can install and set up the library:

1. **Install HUNSPELL**: First, install the external Hunspell package using pip:
   ```bash
   pip install hunspell
   ```

2. **Download Amharic Hunspell Dictionary**: Since Amharic is a complex language, there might not be as many pre-built dictionaries available. You can download an Amharic dictionary from sources like WordNet or other linguistic resources. You may need to specify the path to the downloaded dictionary when setting up the `Hunspell` class.

3. **Use HUNSPELL**: Here's a simple example of how you might use the `hunspell` library to analyze Amharic text:

   ```python
   from hunspell import Hunspell

   # Install the Hunspell library: pip install hunspell

   # Download and save an Amharic dictionary (if needed)

   # Initialize the Hunspell object using the path to the downloaded dictionary file
   hunspell = Hunspell(lang='ah')

   text_amharic = "Ethíopia is a federation of 8 regional states."

   # Check if words are spelled correctly
   misspelled_words = [word for word in text_amharic.split() if not hunspell.spell(word)]
   print("Misspelled words:", misspelled_words)

   # Get suggestions for correcting missed words
   corrections = {word: hunspell.suggest(word) for word in misspelled_words}
   print("Corrections:", corrections)
   ```

4. **Advanced Usage**: The `hunspell` library also provides functions for spell checking, stemming, and sentence splitting, which can be useful for more advanced text analysis tasks.

This setup should help you get started with parsing and analyzing Amharic text in Python using the `hunspell` library. Note that handling complex linguistic data like Amharic might require further customization depending on your specific needs and available resources.
