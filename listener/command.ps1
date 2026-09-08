python -c "
import openwakeword, inspect
from openwakeword.model import Model
print('--- MODELS keys ---')
print(list(openwakeword.MODELS.keys()))
print()
print('--- get_pretrained_model_paths source ---')
print(inspect.getsource(openwakeword.get_pretrained_model_paths))
print()
print('--- Model.__init__ source ---')
print(inspect.getsource(Model.__init__))
"