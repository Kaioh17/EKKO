# import openwakeword, inspect
# from openwakeword.model import Model
# print('--- MODELS keys ---')
# print(list(openwakeword.MODELS.keys()))
# print()
# print('--- get_pretrained_model_paths source ---')
# print(inspect.getsource(openwakeword.get_pretrained_model_paths))
# print()
# print('--- Model.__init__ source ---')
# print(inspect.getsource(Model.__init__))
import inspect
from openwakeword.model import Model

init = Model.__init__
print('has __wrapped__:', hasattr(init, '__wrapped__'))
if hasattr(init, '__wrapped__'):
    print(inspect.signature(init.__wrapped__))
    print(inspect.getsource(init.__wrapped__))

print()
print('freevars:', init.__code__.co_freevars)
if init.__closure__:
    for name, cell in zip(init.__code__.co_freevars, init.__closure__):
        val = cell.cell_contents
        print(f'--- {name} ---')
        if callable(val):
            try:
                print(inspect.signature(val))
                print(inspect.getsource(val))
            except Exception as e:
                print('(could not introspect:', e, ')')
        else:
            print(val)